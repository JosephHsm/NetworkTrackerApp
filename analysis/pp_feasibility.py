"""
핑퐁 예측 가능성 사전 점검 — "핸드오버 순간의 정보만으로, 30초 안에 되돌아갈지(핑퐁) 맞힐 수 있나"

AI 연구 방향 문서(핸드오버_AI_연구방향.md)의 근거 수치를 내는 최소 실험이다. 본 모델이 아니다.
라이브러리 없이 numpy 로지스틱 회귀로 돌리고, 세션 단위로 나눠 교차검증한다(같은 세션이 학습·평가에 섞이지 않게).

라벨  : 핸드오버 A→B 다음 핸드오버가 30초 안에 B→A이면 1 (= 이 핸드오버가 핑퐁의 앞쪽 절반)
특징  : 핸드오버 시점에 이미 알고 있는 값만 쓴다 (직전·직후 RSRP/RSRQ, A 체류 시간, 최근 30초 HO 수,
        같은 기지국 여부, 주파수 변경 여부, 이번 HO 자체가 복귀인지, 직전 이웃 목록에서 B의 순위·여유, 이동수단)

사용법:
    python analysis/pp_feasibility.py
출력: analysis_output/stats/pp_feasibility.json (+ 콘솔 요약)
"""
import glob, json, os
import numpy as np
import pandas as pd

OUT = "analysis_output/stats/pp_feasibility.json"
PP_S = 30
ACTS = ("car", "subway", "walking", "home")


def activity(path):
    for a in ACTS:
        if path.endswith(f"_{a}.csv"):
            return a
    return "car"


def nbr_rank(js, key):
    """직전 이웃 목록에서 (earfcn, pci)=key 셀의 같은 주파수 순위(1부터)와 RSRP — 없으면 (0, None)"""
    try:
        a = json.loads(js)
    except Exception:
        return 0, None
    same = sorted([x for x in a if isinstance(x, dict) and x.get("type") == "LTE" and x.get("rsrp") is not None
                   and x.get("earfcn") == key[0]], key=lambda x: -x["rsrp"])
    for i, x in enumerate(same):
        if x.get("pci") == key[1]:
            return i + 1, x["rsrp"]
    return 0, None


def events():
    rows = []
    for si, f in enumerate(sorted(glob.glob("trackingcsv/new/*.csv"))):
        d = pd.read_csv(f, low_memory=False).sort_values("timestamp", kind="stable").reset_index(drop=True)
        t = d["timestamp"].to_numpy() / 1000.0
        cell = d["serving_cell_id"].to_numpy()
        ho = np.where(d["handover_detected"].astype(str).str.lower().eq("true").to_numpy())[0]
        pp = d["ping_pong_detected"].astype(str).str.lower().eq("true").to_numpy()
        pj = d["prev_neighbors_json"] if "prev_neighbors_json" in d else None
        act = activity(f)
        for k, i in enumerate(ho):
            if i == 0:
                continue
            a, b = cell[i - 1], cell[i]
            nxt = ho[k + 1] if k + 1 < len(ho) else None
            label = int(nxt is not None and t[nxt] - t[i] < PP_S and cell[nxt] == a)
            prev_ho_t = t[ho[k - 1]] if k > 0 else t[0]
            js = pj[i] if pj is not None and isinstance(pj[i], str) and pj[i] not in ("", "[]") else d["neighbors_json"][i - 1]
            rk, trsrp = nbr_rank(js, (int(d["serving_freq_arfcn"][i]), int(d["serving_pci"][i])))
            rp = float(d["rsrp_dbm"][i - 1])
            rows.append(dict(
                session=si, act=act, y=label,
                rsrp_prev=rp, rsrp_new=float(d["rsrp_dbm"][i]),
                rsrq_prev=float(d["rsrq_db"][i - 1]), rsrq_new=float(d["rsrq_db"][i]),
                dwell=min(t[i] - prev_ho_t, 300.0),
                ho30=float(d["ho_count_30s"][i - 1]),
                same_enb=float(int(a) // 256 == int(b) // 256),
                inter_freq=float(d["serving_freq_arfcn"][i] != d["serving_freq_arfcn"][i - 1]),
                is_return=float(pp[i]),
                tgt_seen=float(rk > 0), tgt_top=float(rk == 1),
                tgt_margin=(trsrp - rp) if trsrp is not None else 0.0,
            ))
    return pd.DataFrame(rows)


FEATS = ["rsrp_prev", "rsrp_new", "rsrq_prev", "rsrq_new", "dwell", "ho30", "same_enb", "inter_freq",
         "is_return", "tgt_seen", "tgt_top", "tgt_margin", "act_car", "act_subway", "act_walking"]


def auc(y, s):
    """순위 기반 AUC (동점은 평균 순위)"""
    r = pd.Series(s).rank().to_numpy()
    n1 = y.sum(); n0 = len(y) - n1
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)) if n1 and n0 else float("nan")


def fit_logreg(X, y, l2=1.0, iters=3000, lr=0.1):
    w = np.zeros(X.shape[1]); b = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(X @ w + b)))
        g = p - y
        w -= lr * (X.T @ g / len(y) + l2 * w / len(y))
        b -= lr * g.mean()
    return w, b


def main():
    E = events()
    for a in ("car", "subway", "walking"):
        E["act_" + a] = (E["act"] == a).astype(float)
    X = E[FEATS].to_numpy(float); y = E["y"].to_numpy(int)

    # 세션 단위 5겹 교차검증
    sess = E["session"].unique()
    rng = np.random.default_rng(0); rng.shuffle(sess)
    folds = np.array_split(sess, 5)
    score = np.zeros(len(E))
    for fs in folds:
        te = E["session"].isin(fs).to_numpy(); tr = ~te
        mu, sd = X[tr].mean(0), X[tr].std(0) + 1e-9
        w, b = fit_logreg((X[tr] - mu) / sd, y[tr])
        score[te] = (X[te] - mu) / sd @ w + b

    # 단일 규칙 기준선: "직전 셀 체류가 짧을수록 핑퐁" (dwell 하나로 순위)
    base = {"체류 시간만": auc(y, -E["dwell"].to_numpy()),
            "이번 HO가 복귀인가만": auc(y, E["is_return"].to_numpy()),
            "목표 셀 여유(dB)만": auc(y, -E["tgt_margin"].to_numpy())}
    model_auc = auc(y, score)

    # 상위 k% 차단 시: 걸러지는 핑퐁 비율 vs 막히는 정상 HO 비율
    order = np.argsort(-score)
    cut = {}
    for q in (0.1, 0.2, 0.3):
        top = order[: int(len(order) * q)]
        cut[f"top{int(q*100)}"] = dict(pp_caught=round(float(y[top].sum() / y.sum()), 3),
                                        normal_blocked=round(float((1 - y[top]).sum() / (1 - y).sum()), 3))
    # 전체 표본으로 계수(해석용)
    mu, sd = X.mean(0), X.std(0) + 1e-9
    w, _ = fit_logreg((X - mu) / sd, y)
    coef = dict(sorted(zip(FEATS, np.round(w, 3)), key=lambda kv: -abs(kv[1])))

    out = dict(n=int(len(E)), positives=int(y.sum()), pos_rate=round(float(y.mean()), 3),
               sessions=int(len(sess)), auc_logreg_cv=round(model_auc, 3),
               auc_single={k: round(v, 3) for k, v in base.items()}, block=cut, coef=coef)
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=1, default=float)

    print(f"[핑퐁 예측] HO {out['n']}건, 핑퐁 앞절반 {out['positives']}건 ({out['pos_rate']:.1%}), 세션 {out['sessions']}개")
    print(f"  로지스틱 회귀 AUC (세션 5겹) {out['auc_logreg_cv']}")
    for k, v in out["auc_single"].items():
        print(f"  기준선 AUC — {k}: {v}")
    for k, v in cut.items():
        print(f"  점수 {k}% 차단: 핑퐁 {v['pp_caught']:.0%} 걸러냄 / 정상 HO {v['normal_blocked']:.0%} 막힘")
    print("  계수(표준화):", coef)
    print(f"→ {OUT}")


if __name__ == "__main__":
    main()
