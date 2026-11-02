"""
핸드오버 AI 모델 비교 — XGBoost / RNN / LSTM / GRU / Q-learning (교수님 todo 1번)

A. 핑퐁 예측(분류)   : 핸드오버 A→B 순간에 "30초 안에 A로 되돌아갈지"(핑퐁)와
                      "10초 뒤 RSRP가 직전보다 3 dB 이상 낮을지"(손해 핸드오버)를 맞힌다.
                      로지스틱 회귀(기준선, pp_feasibility.py와 같은 특징 15개) · XGBoost · RNN · LSTM · GRU
B. 신호 예측(회귀)   : 지금까지 60초(12행)를 보고 5·10·15초 뒤 서빙 RSRP를 예측한다.
                      지금 값 유지(기준선) · XGBoost · RNN · LSTM · GRU
C. 핸드오버 정책     : 재생 시뮬레이터(ho_review.py와 같은 방식)에서 이웃이 3 dB 세지는 순간마다
                      "옮길지/보류할지"를 고른다. 실제 망 · A3 규칙 · A3+핑퐁벌점 · XGBoost 차단 · Q-learning
(Jev는 유료 API라 이번에는 비워 두었다 — 다음 달 추가 예정)

공통: 세션 단위 5겹 교차검증 (같은 세션이 학습·평가에 섞이지 않게). 신경망은 시드 3개 평균.

사용법:
    python analysis/model_compare.py            # 전부
    python analysis/model_compare.py A B        # 일부만
출력: analysis_output/stats/model_compare.json (+ 콘솔 요약)
      analysis_output/stats/model_compare_pred.npz (그림용 예측값)
"""
import glob, json, os, sys, time
import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pp_feasibility as ppf
import ho_review as hr

OUT = "analysis_output/stats/model_compare.json"
PRED = "analysis_output/stats/model_compare_pred.npz"
W = 12                  # 시퀀스 길이 (≈ 60초)
HORIZONS = (5, 10, 15)  # 신호 예측 시점 (초)
SEEDS = (0, 1, 2)
LOSS_DB, LOSS_S = -3, 10
STALE_MS = hr.STALE_MS

# 시퀀스 입력 (행 단위)
SEQ_COLS = ["rsrp", "rsrq", "nbr_gap", "nbr_cnt", "ho", "pp", "dt", "stale"]


# ── 데이터 ───────────────────────────────────────────────────────────────

def load_frames():
    """파일별 행 단위 특징 행렬 (시퀀스 입력용)"""
    F = {}
    for f in sorted(glob.glob("trackingcsv/new/*.csv")):
        d = pd.read_csv(f, low_memory=False).sort_values("timestamp", kind="stable").reset_index(drop=True)
        t = d["timestamp"].to_numpy() / 1000.0
        rsrp = d["rsrp_dbm"].astype(float).to_numpy()
        X = np.column_stack([
            rsrp,
            d["rsrq_db"].astype(float).to_numpy(),
            # best_nbr_rsrp는 주파수 무관 최강 이웃 — 서빙과 같은 주파수라는 보장이 없어 "비교"가 아니라 입력 특징으로만 쓴다
            np.nan_to_num(d["best_nbr_rsrp_dbm"].astype(float).to_numpy() - rsrp, nan=-30.0),
            d["neighbor_count"].astype(float).to_numpy(),
            hr.as_bool(d["handover_detected"]).astype(float),
            hr.as_bool(d["ping_pong_detected"]).astype(float),
            np.clip(np.diff(t, prepend=t[0]), 0, 30),
            (d["cell_info_age_ms"].to_numpy() > STALE_MS).astype(float),
        ])
        X[:, 0] = pd.Series(X[:, 0]).ffill().bfill().to_numpy()
        X[:, 1] = pd.Series(X[:, 1]).ffill().bfill().to_numpy()
        F[f] = dict(t=t, X=X, stale=X[:, 7] > 0, act=hr.activity(f))
    return F


def window(X, end):
    """end 행까지(포함) W행. 앞이 모자라면 0으로 채우고 마스크 열을 붙인다."""
    s = max(0, end - W + 1)
    w = X[s:end + 1]
    out = np.zeros((W, X.shape[1] + 1))
    out[W - len(w):, :-1] = w
    out[W - len(w):, -1] = 1
    return out


def ho_dataset(F):
    E = ppf.events()
    for a in ("car", "subway", "walking"):
        E["act_" + a] = (E["act"] == a).astype(float)
    seq, yl = [], []
    for r in E.itertuples():
        fr = F[r.file]
        seq.append(window(fr["X"], r.row - 1))          # 핸드오버 직전까지만 (미래 정보 없음)
        t, rs = fr["t"], fr["X"][:, 0]
        k = np.searchsorted(t, t[r.row] + LOSS_S)
        ok = k < len(t) and t[k] - t[r.row] <= LOSS_S + 5 and not fr["stale"][k]
        yl.append(float(rs[k] - r.rsrp_prev <= LOSS_DB) if ok else np.nan)
    E["y_loss"] = yl
    return E, np.stack(seq)


def rsrp_dataset(F):
    rows, seq, Y = [], [], []
    for si, (f, fr) in enumerate(F.items()):
        t, rs, st = fr["t"], fr["X"][:, 0], fr["stale"]
        for j in range(len(t)):
            if st[j]:
                continue
            y = []
            for h in HORIZONS:
                k = np.searchsorted(t, t[j] + h - 1)
                ok = k < len(t) and t[k] - t[j] <= h + 2.5 and not st[k]
                y.append(rs[k] - rs[j] if ok else np.nan)     # 변화량을 예측 (지금 값 유지 = 0)
            if np.all(np.isnan(y)):
                continue
            rows.append(dict(session=si, act=fr["act"], rsrp=rs[j]))
            seq.append(window(fr["X"], j))
            Y.append(y)
    R = pd.DataFrame(rows)
    for a in ("car", "subway", "walking"):
        R["act_" + a] = (R["act"] == a).astype(float)
    return R, np.stack(seq), np.array(Y)


def session_folds(sessions, k=5, seed=0):
    s = np.unique(sessions)
    rng = np.random.default_rng(seed); rng.shuffle(s)
    return [np.isin(sessions, fs) for fs in np.array_split(s, k)]


# ── 지표 ─────────────────────────────────────────────────────────────────

def auc(y, s):
    return ppf.auc(y, s)


def ap(y, s):
    o = np.argsort(-s); y = y[o]
    prec = np.cumsum(y) / np.arange(1, len(y) + 1)
    return float((prec * y).sum() / max(y.sum(), 1))


def ece(y, p, bins=10):
    e = 0.0
    for lo in np.linspace(0, 1, bins, endpoint=False):
        m = (p >= lo) & (p < lo + 1 / bins)
        if m.any():
            e += m.mean() * abs(p[m].mean() - y[m].mean())
    return float(e)


def block(y, s):
    o = np.argsort(-s); out = {}
    for q in (0.1, 0.2, 0.3):
        top = o[: int(len(o) * q)]
        out[f"top{int(q * 100)}"] = dict(pp_caught=round(float(y[top].sum() / y.sum()), 3),
                                          normal_blocked=round(float((1 - y[top]).sum() / (1 - y).sum()), 3))
    return out


def cls_metrics(y, p):
    return dict(auc=round(auc(y, p), 3), ap=round(ap(y, p), 3),
                brier=round(float(np.mean((p - y) ** 2)), 4), ece=round(ece(y, p), 4), block=block(y, p))


# ── 모델 ─────────────────────────────────────────────────────────────────

def logreg(Xtr, ytr, Xte):
    mu, sd = Xtr.mean(0), Xtr.std(0) + 1e-9
    w, b = ppf.fit_logreg((Xtr - mu) / sd, ytr)
    return 1 / (1 + np.exp(-((Xte - mu) / sd @ w + b)))


def xgb_cls(Xtr, ytr, Xte, seed=0):
    import xgboost as xgb
    m = xgb.XGBClassifier(n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8,
                          colsample_bytree=0.8, min_child_weight=5, reg_lambda=1.0,
                          random_state=seed, n_jobs=8, verbosity=0)
    m.fit(Xtr, ytr)
    return m.predict_proba(Xte)[:, 1], m


def xgb_reg(Xtr, ytr, Xte, seed=0):
    import xgboost as xgb
    m = xgb.XGBRegressor(n_estimators=400, max_depth=4, learning_rate=0.05, subsample=0.8,
                         colsample_bytree=0.8, min_child_weight=10, objective="reg:absoluteerror",
                         random_state=seed, n_jobs=8, verbosity=0)
    m.fit(Xtr, ytr)
    return m.predict(Xte)


def flat(seq, static):
    """XGBoost용: 시퀀스 마지막 6행을 펼쳐서 정적 특징과 붙인다"""
    return np.hstack([seq[:, -6:, :].reshape(len(seq), -1), static])


def make_net(kind, n_in, n_static, n_out, hidden=32):
    import torch.nn as nn
    import torch

    class Net(nn.Module):
        def __init__(self):
            super().__init__()
            cell = dict(rnn=nn.RNN, lstm=nn.LSTM, gru=nn.GRU)[kind]
            self.rnn = cell(n_in, hidden, batch_first=True)
            self.head = nn.Sequential(nn.Linear(hidden + n_static, 32), nn.ReLU(), nn.Dropout(0.2),
                                      nn.Linear(32, n_out))

        def forward(self, x, s):
            h = self.rnn(x)[0][:, -1]
            return self.head(torch.cat([h, s], 1))
    return Net()


def train_net(kind, seq_tr, st_tr, y_tr, seq_te, st_te, task, seed, sess_tr, epochs=80):
    """task='cls'(BCE) | 'reg'(L1, NaN 타깃 마스크). 학습 세션 일부를 떼어 조기 종료."""
    import torch
    torch.manual_seed(seed); np.random.seed(seed)
    torch.set_num_threads(8)
    # 정규화 (학습 세트 기준, 마스크 열 제외)
    mu = seq_tr[..., :-1].reshape(-1, seq_tr.shape[2] - 1).mean(0)
    sd = seq_tr[..., :-1].reshape(-1, seq_tr.shape[2] - 1).std(0) + 1e-6
    smu, ssd = st_tr.mean(0), st_tr.std(0) + 1e-6

    def prep(q, s):
        q = q.copy(); q[..., :-1] = (q[..., :-1] - mu) / sd * q[..., -1:]
        return (torch.tensor(q, dtype=torch.float32), torch.tensor((s - smu) / ssd, dtype=torch.float32))

    va = session_folds(sess_tr, k=6, seed=seed + 100)[0]
    tr = ~va
    Xs, Ss = prep(seq_tr, st_tr)
    Xe, Se = prep(seq_te, st_te)
    Y = torch.tensor(y_tr if y_tr.ndim > 1 else y_tr[:, None], dtype=torch.float32)
    net = make_net(kind, Xs.shape[2], Ss.shape[1], Y.shape[1])
    opt = torch.optim.Adam(net.parameters(), lr=2e-3, weight_decay=1e-4)

    def loss_fn(o, y):
        if task == "cls":
            return torch.nn.functional.binary_cross_entropy_with_logits(o, y)
        m = ~torch.isnan(y)
        return (o[m] - y[m]).abs().mean()

    itr = np.where(tr)[0]; iva = np.where(va)[0]
    best, best_state, bad = 1e9, None, 0
    for ep in range(epochs):
        net.train()
        np.random.shuffle(itr)
        for b in range(0, len(itr), 64):
            ix = itr[b:b + 64]
            opt.zero_grad()
            l = loss_fn(net(Xs[ix], Ss[ix]), Y[ix])
            l.backward(); opt.step()
        net.eval()
        with torch.no_grad():
            v = float(loss_fn(net(Xs[iva], Ss[iva]), Y[iva]))
        if v < best - 1e-4:
            best, bad = v, 0
            best_state = {k: x.clone() for k, x in net.state_dict().items()}
        else:
            bad += 1
            if bad >= 10:
                break
    net.load_state_dict(best_state)
    net.eval()
    with torch.no_grad():
        o = net(Xe, Se).numpy()
    return 1 / (1 + np.exp(-o[:, 0])) if task == "cls" else o


# ── A. 핑퐁·손해 핸드오버 분류 ───────────────────────────────────────────

def part_a(F):
    E, SEQ = ho_dataset(F)
    STATIC = E[ppf.FEATS].to_numpy(float)
    folds = session_folds(E["session"].to_numpy())
    res, preds = {}, {}
    for label in ("y", "y_loss"):
        ok = E[label].notna().to_numpy()
        y = E[label].to_numpy()[ok].astype(int)
        seq, st, sess = SEQ[ok], STATIC[ok], E["session"].to_numpy()[ok]
        fo = [f[ok] for f in folds]
        P = {m: np.zeros((len(SEEDS), len(y))) for m in ("logreg", "xgboost_static", "xgboost", "rnn", "lstm", "gru")}
        for te in fo:
            tr = ~te
            P["logreg"][:, te] = logreg(st[tr], y[tr], st[te])
            for si, seed in enumerate(SEEDS):
                P["xgboost_static"][si, te] = xgb_cls(st[tr], y[tr], st[te], seed)[0]      # 특징 15개만
                P["xgboost"][si, te] = xgb_cls(flat(seq[tr], st[tr]), y[tr], flat(seq[te], st[te]), seed)[0]
                for k in ("rnn", "lstm", "gru"):
                    P[k][si, te] = train_net(k, seq[tr], st[tr], y[tr].astype(float), seq[te], st[te],
                                             "cls", seed, sess[tr])
        name = "pingpong" if label == "y" else "loss"
        res[name] = dict(n=int(len(y)), pos=int(y.sum()), pos_rate=round(float(y.mean()), 3), models={})
        for m, p in P.items():
            per_seed = [auc(y, p[i]) for i in range(len(SEEDS))]
            mm = cls_metrics(y, p.mean(0))
            mm["auc_seed_mean"], mm["auc_seed_std"] = round(float(np.mean(per_seed)), 3), round(float(np.std(per_seed)), 3)
            res[name]["models"][m] = mm
            preds[f"A_{name}_{m}"] = p.mean(0)
        preds[f"A_{name}_y"] = y
        print(f"[A {name}] n={len(y)} 양성 {y.sum()} ({y.mean():.1%})")
        for m, v in res[name]["models"].items():
            print(f"   {m:8s} AUC {v['auc']:.3f} (시드 {v['auc_seed_mean']:.3f}±{v['auc_seed_std']:.3f})"
                  f"  AP {v['ap']:.3f}  Brier {v['brier']:.4f}  ECE {v['ece']:.3f}"
                  f"  상위20% 차단: 걸러냄 {v['block']['top20']['pp_caught']:.0%} / 정상막힘 {v['block']['top20']['normal_blocked']:.0%}")
    # XGBoost 특징 중요도 (핑퐁, 전체 학습)
    y = E["y"].to_numpy().astype(int)
    _, m = xgb_cls(flat(SEQ, STATIC), y, flat(SEQ[:1], STATIC[:1]))
    names = [f"{c}[t-{5 - i}]" for i in range(6) for c in SEQ_COLS + ["mask"]] + ppf.FEATS
    imp = sorted(zip(names, m.feature_importances_), key=lambda kv: -kv[1])[:15]
    res["pingpong"]["xgb_importance"] = {k: round(float(v), 4) for k, v in imp}
    return res, preds


# ── B. RSRP 예측 ─────────────────────────────────────────────────────────

def part_b(F):
    R, SEQ, Y = rsrp_dataset(F)
    ST = R[["act_car", "act_subway", "act_walking"]].to_numpy(float)
    folds = session_folds(R["session"].to_numpy())
    sess = R["session"].to_numpy()
    P = {m: np.full((len(SEEDS),) + Y.shape, np.nan) for m in ("xgboost", "rnn", "lstm", "gru")}
    for te in folds:
        tr = ~te
        for si, seed in enumerate(SEEDS):
            for h in range(len(HORIZONS)):
                ok = tr & ~np.isnan(Y[:, h])
                P["xgboost"][si, te, h] = xgb_reg(flat(SEQ[ok], ST[ok]), Y[ok, h], flat(SEQ[te], ST[te]), seed)
            for k in ("rnn", "lstm", "gru"):
                P[k][si, te] = train_net(k, SEQ[tr], ST[tr], Y[tr], SEQ[te], ST[te], "reg", seed, sess[tr], epochs=40)
    res = dict(n=int(len(R)), horizons=list(HORIZONS), models={})
    acts = R["act"].to_numpy()

    def mae(pred):
        out = {}
        for h, hs in enumerate(HORIZONS):
            ok = ~np.isnan(Y[:, h])
            out[f"{hs}s"] = round(float(np.abs(pred[ok, h] - Y[ok, h]).mean()), 3)
            for a in ("car", "subway", "walking"):
                m = ok & (acts == a)
                if m.any():
                    out[f"{hs}s_{a}"] = round(float(np.abs(pred[m, h] - Y[m, h]).mean()), 3)
        return out

    res["models"]["persistence"] = mae(np.zeros_like(Y))
    for m, p in P.items():
        res["models"][m] = mae(p.mean(0))
        res["models"][m]["seed_std_10s"] = round(float(np.std([mae(p[i])["10s"] for i in range(len(SEEDS))])), 3)
    print(f"[B RSRP 예측] 표본 {len(R)}")
    for m, v in res["models"].items():
        print(f"   {m:11s} MAE " + "  ".join(f"{h}s {v[f'{h}s']:.2f}" for h in HORIZONS) + " dB")
    preds = {"B_y": Y, "B_act": acts.astype(str), **{f"B_{m}": p.mean(0) for m, p in P.items()}}
    return res, preds


# ── C. 핸드오버 정책 (재생 시뮬레이터) ───────────────────────────────────

GATE_FEATS = ["rsrp_prev", "rsrp_new", "dwell", "ho30", "is_return", "tgt_margin", "act_car", "act_subway", "act_walking"]
REWARD = dict(ho=1.0, pp=3.0, weak=5.0, gap=0.1)   # 행(≈5초)마다: −핸드오버 −핑퐁 −약전계(≤−105) −최강셀 대비 dB


def rsrp_bin(s):
    return 0 if s <= -105 else 1 if s <= -95 else 2 if s <= -85 else 3


def state(s, margin, is_ret, dwell):
    return (rsrp_bin(s), 0 if margin < 6 else 1 if margin < 10 else 2, int(is_ret),
            0 if dwell < 10 else 1 if dwell < 30 else 2)


def run_policy(fr, act, decide, learn=None):
    """
    ho_review.replay(hyst=3, ttt=1)과 같은 재생. 같은 주파수 이웃이 3 dB 이상 세진 행이 '결정 시점'이고
    decide(ctx) → True(옮김)/False(보류). learn이 있으면 Q-learning 갱신용 콜백(보상 누적)을 부른다.
    """
    cur, left, last_ho_t = None, [], None
    n_ho = n_pp = n_forced = 0
    rs, gap = [], []
    hist = []         # 최근 핸드오버 시각 (ho30)
    for t, sk, m, fresh in fr:
        if cur is None:
            cur, last_ho_t = sk, t
        new, r = None, 0.0
        if cur not in m:
            new, n_forced = sk, n_forced + 1
        elif fresh:
            s = m[cur]
            best = max((k for k in m if k != cur and k[0] == cur[0]), key=m.get, default=None)
            if best is not None and m[best] - s >= 3:
                is_ret = any(k == best and t - ts < hr.PP_S for k, ts in left)
                ctx = dict(s=s, tgt=m[best], margin=m[best] - s, is_ret=is_ret, dwell=min(t - last_ho_t, 300.0),
                           ho30=sum(t - x < 30 for x in hist), act=act)
                if decide(ctx):
                    new = best
        if new is not None and new != cur:
            n_ho += 1; r -= REWARD["ho"]
            if any(k == new and t - ts < hr.PP_S for k, ts in left):
                n_pp += 1; r -= REWARD["pp"]
            left = [(cur, t)] + [(k, ts) for k, ts in left if t - ts < hr.PP_S]
            hist = [x for x in hist if t - x < 30] + [t]
            cur, last_ho_t = new, t
        if fresh and cur in m:
            g = max(v for k, v in m.items() if k[0] == cur[0]) - m[cur]
            rs.append(m[cur]); gap.append(g)
            r -= REWARD["weak"] * (m[cur] <= hr.WEAK[0]) + REWARD["gap"] * min(g, 15)
        if learn:
            learn(r)
    if learn:
        learn(None)
    rs = np.array(rs, float)
    return dict(ho=n_ho, pp=n_pp, forced=n_forced, rows=len(rs), rsrp_sum=float(rs.sum()),
                weak105=int((rs <= hr.WEAK[0]).sum()), gap_sum=float(np.sum(gap)))


class QAgent:
    def __init__(self, alpha=0.1, gamma=0.9, seed=0):
        self.Q, self.alpha, self.gamma = {}, alpha, gamma
        self.rng = np.random.default_rng(seed)
        self.eps, self.pending, self.ret = 0.0, None, 0.0

    def q(self, s):
        return self.Q.setdefault(s, np.zeros(2))

    def decide(self, ctx):
        s = state(ctx["s"], ctx["margin"], ctx["is_ret"], ctx["dwell"])
        a = int(self.rng.integers(2)) if self.rng.random() < self.eps else int(np.argmax(self.q(s)))
        if self.training:
            self._update(s)
            self.pending = [s, a, 0.0, 1.0]       # 상태, 행동, 누적 보상, 할인
        return a == 1

    def _update(self, s_next):
        if self.pending is None:
            return
        s, a, R, disc = self.pending
        target = R + (disc * self.gamma * np.max(self.q(s_next)) if s_next is not None else 0.0)
        self.q(s)[a] += self.alpha * (target - self.q(s)[a])
        self.pending = None

    def learn(self, r):
        if r is None:                     # 에피소드 끝
            self._update(None); return
        self.ret += r
        if self.pending is not None:
            self.pending[2] += self.pending[3] * r
            self.pending[3] *= self.gamma


def summarize(acc):
    return dict(ho=acc["ho"], pp=acc["pp"], forced=acc["forced"],
                rsrp_mean=round(acc["rsrp_sum"] / acc["rows"], 2),
                weak105_share=round(acc["weak105"] / acc["rows"], 4),
                gap_to_best=round(acc["gap_sum"] / acc["rows"], 2))


def part_c(F, epochs=60):
    files = sorted(F)
    sessions = []
    for f in files:
        d = hr.load(f)
        sessions.append((F[f]["act"], hr.frames(d)))
    sid = np.arange(len(files))
    E = ppf.events()
    for a in ("car", "subway", "walking"):
        E["act_" + a] = (E["act"] == a).astype(float)
    E["sid"] = E["file"].map({f: i for i, f in enumerate(files)})

    tot = {}
    curve = []

    def add(name, r):
        a = tot.setdefault(name, {})
        for k, v in r.items():
            a[k] = a.get(k, 0) + v

    for fi, te in enumerate(session_folds(sid)):
        tr_ids, te_ids = sid[~te], sid[te]
        # XGBoost 차단기 — 재생 중에도 계산되는 특징만으로 학습 (학습 세션의 실제 핸드오버)
        Etr = E[E["sid"].isin(tr_ids)]
        _, gate = xgb_cls(Etr[GATE_FEATS].to_numpy(float), Etr["y"].to_numpy(int), Etr[GATE_FEATS].to_numpy(float)[:1])
        thr = np.quantile(gate.predict_proba(Etr[GATE_FEATS].to_numpy(float))[:, 1], 0.8)   # 상위 20% 보류

        def gate_decide(c):
            x = np.array([[c["s"], c["tgt"], c["dwell"], c["ho30"], float(c["is_ret"]), c["margin"],
                           c["act"] == "car", c["act"] == "subway", c["act"] == "walking"]], float)
            return gate.predict_proba(x)[0, 1] < thr

        # Q-learning — 학습 세션에서 ε-greedy로 반복 재생
        ag = QAgent(seed=fi)
        ag.training = True
        rng = np.random.default_rng(fi)
        for ep in range(epochs):
            ag.eps = max(0.02, 0.3 * (1 - ep / (epochs * 0.7)))
            ag.ret = 0.0
            for i in rng.permutation(tr_ids):
                act, fr = sessions[i]
                run_policy(fr, act, ag.decide, ag.learn)
            if fi == 0:
                curve.append(ag.ret)
        ag.training, ag.eps = False, 0.0

        for i in te_ids:
            act, fr = sessions[i]
            add("실제 망", {k: v for k, v in hr.replay(fr, network=True).items() if k != "forced_last" and k != "weak110"})
            add("A3 3dB", run_policy(fr, act, lambda c: True))
            add("A3 3dB + 핑퐁벌점 6dB", run_policy(fr, act, lambda c: not c["is_ret"] or c["margin"] >= 9))
            add("A3 3dB + XGBoost 차단(상위 20%)", run_policy(fr, act, gate_decide))
            add("A3 3dB + Q-learning", run_policy(fr, act, ag.decide))
        if fi == 0:
            qtab = {str(k): [round(float(x), 2) for x in v] for k, v in sorted(ag.Q.items())}
        print(f"  [C] fold {fi + 1}/5 완료")

    res = dict(reward=REWARD, epochs=epochs, policies={k: summarize(v) for k, v in tot.items()},
               q_curve_fold0=[round(x, 1) for x in curve], q_table_fold0=qtab)
    base = res["policies"]["실제 망"]
    print("[C 정책 재생] (세션 5겹, 평가 세션 합계)")
    for k, v in res["policies"].items():
        print(f"   {k:30s} HO {v['ho']:5d} ({v['ho'] / base['ho'] - 1:+.0%})  핑퐁 {v['pp']:4d} ({v['pp'] / base['pp'] - 1:+.0%})"
              f"  강제 {v['forced']:4d}  평균RSRP {v['rsrp_mean']:7.2f}  ≤-105 {v['weak105_share']:.2%}  최강셀대비 {v['gap_to_best']:.2f}")
    return res


def main():
    parts = sys.argv[1:] or ["A", "B", "C"]
    t0 = time.time()
    F = load_frames()
    out = json.load(open(OUT, encoding="utf-8")) if os.path.exists(OUT) else {}
    preds = dict(np.load(PRED, allow_pickle=True)) if os.path.exists(PRED) else {}
    if "A" in parts:
        out["A"], p = part_a(F); preds.update(p)
    if "B" in parts:
        out["B"], p = part_b(F); preds.update(p)
    if "C" in parts:
        out["C"] = part_c(F)
    out["jev"] = "미적용 (유료 API, 다음 달 추가 예정)"
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)
    np.savez(PRED, **preds)
    print(f"→ {OUT}  ({time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main()
