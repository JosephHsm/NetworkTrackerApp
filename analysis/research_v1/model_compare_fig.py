"""
model_compare.py 결과 그림 — 보고서(모델_비교_보고)용 PNG

사용법:
    python analysis/model_compare_fig.py
출력: analysis_output/model_compare/*.png
"""
import json, os
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker

STATS = "analysis_output/stats/model_compare.json"
PRED = "analysis_output/stats/model_compare_pred.npz"
OUTDIR = "analysis_output/model_compare"

plt.rcParams.update({"font.family": "Malgun Gothic", "axes.unicode_minus": False, "font.size": 10,
                     "axes.spines.top": False, "axes.spines.right": False,
                     "axes.edgecolor": "#8a8984", "axes.labelcolor": "#52514e",
                     "xtick.color": "#52514e", "ytick.color": "#52514e",
                     "axes.grid": True, "grid.color": "#e6e5e0", "grid.linewidth": 0.8})
INK, MUTED, BASE = "#0b0b0b", "#52514e", "#8a8984"
# 모델 색은 고정 (기준선은 회색, 나머지는 범주 순서대로)
COLOR = {"logreg": BASE, "persistence": BASE, "xgboost_static": "#2a78d6", "xgboost": "#2a78d6",
         "rnn": "#eb6834", "lstm": "#1baf7a", "gru": "#6250d6"}
LABEL = {"logreg": "로지스틱 회귀(기준선)", "persistence": "지금 값 유지(기준선)",
         "xgboost_static": "XGBoost", "xgboost": "XGBoost", "rnn": "RNN", "lstm": "LSTM", "gru": "GRU"}


def roc(y, s):
    o = np.argsort(-s); y = y[o]
    tpr = np.concatenate([[0], np.cumsum(y) / y.sum()])
    fpr = np.concatenate([[0], np.cumsum(1 - y) / (1 - y).sum()])
    return fpr, tpr


def fig_roc(S, P):
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.4))
    for ax, name, title in zip(axes, ("pingpong", "loss"), ("핑퐁 예측", "손해 핸드오버 예측")):
        y = P[f"A_{name}_y"]
        for m in ("logreg", "xgboost_static", "rnn", "lstm", "gru"):
            f, t = roc(y, P[f"A_{name}_{m}"])
            a = S["A"][name]["models"][m]["auc"]
            ax.plot(f, t, color=COLOR[m], lw=2, ls="--" if m == "logreg" else "-",
                    label=f"{LABEL[m]}  AUC {a:.3f}")
        ax.plot([0, 1], [0, 1], color="#c3c2b7", lw=1)
        ax.set_title(title, color=INK, loc="left")
        ax.set_xlabel("함께 막히는 정상 핸드오버 비율")
        ax.set_ylabel("걸러지는 대상 비율")
        ax.legend(frameon=False, fontsize=8.5, loc="lower right", labelcolor=MUTED)
        ax.set_aspect("equal")
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/A_roc.png", dpi=150); plt.close(fig)


def fig_rsrp(S):
    B = S["B"]
    fig, ax = plt.subplots(figsize=(6.4, 4))
    hs = B["horizons"]
    for m in ("persistence", "xgboost", "rnn", "lstm", "gru"):
        v = [B["models"][m][f"{h}s"] for h in hs]
        ax.plot(hs, v, color=COLOR[m], lw=2, marker="o", ms=7, ls="--" if m == "persistence" else "-",
                label=LABEL[m])
    ax.set_xticks(hs, [f"{h}초 뒤" for h in hs])
    ax.set_ylabel("평균 오차 MAE (dB)")
    ax.set_title("서빙 RSRP 예측 오차 (낮을수록 좋음)", color=INK, loc="left")
    ax.legend(frameon=False, fontsize=9, labelcolor=MUTED)
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/B_rsrp_mae.png", dpi=150); plt.close(fig)


def fig_policy(S):
    pol = S["C"]["policies"]
    names = list(pol)
    short = {"실제 망": "실제 망", "A3 3dB": "A3 규칙", "A3 3dB + 핑퐁벌점 6dB": "A3+핑퐁벌점",
             "A3 3dB + XGBoost 차단(상위 20%)": "A3+XGBoost 차단", "A3 3dB + Q-learning": "A3+Q-learning"}
    lab = [short.get(n, n) for n in names]
    hl = {"A3 3dB + XGBoost 차단(상위 20%)": "#2a78d6", "A3 3dB + Q-learning": "#6250d6"}
    col = [hl.get(n, BASE) for n in names]
    panels = [("ho", "핸드오버 수", "{:.0f}"), ("pp", "핑퐁 수", "{:.0f}"),
              ("weak105_share", "약전계(≤-105 dBm) 시간 비율", "{:.2%}"), ("rsrp_mean", "평균 RSRP (dBm)", "{:.1f}")]
    fig, axes = plt.subplots(1, 4, figsize=(14, 4.2), sharey=True)
    yy = np.arange(len(names))[::-1]
    for ax, (k, title, fmt) in zip(axes, panels):
        v = np.array([pol[n][k] for n in names], float)
        if k == "rsrp_mean":
            lo = np.floor(v.min()) - 1
            ax.barh(yy, v - lo, left=lo, color=col, height=0.6)
            ax.set_xlim(lo, np.ceil(v.max()) + 0.8)
        else:
            ax.barh(yy, v, color=col, height=0.6)
            ax.set_xlim(0, v.max() * 1.25)
            if k == "weak105_share":
                ax.xaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=1))
        for y, x in zip(yy, v):
            ax.text(x, y, " " + fmt.format(x), va="center", fontsize=8.5, color=MUTED)
        ax.set_title(title, color=INK, loc="left", fontsize=10)
        ax.grid(axis="y", visible=False)
    axes[0].set_yticks(yy, lab)
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/C_policy.png", dpi=150); plt.close(fig)


def fig_qcurve(S):
    c = S["C"]["q_curve_fold0"]
    fig, ax = plt.subplots(figsize=(6.4, 3.6))
    ax.plot(np.arange(1, len(c) + 1), c, color="#6250d6", lw=2)
    ax.set_xlabel("학습 반복(학습 세션 전체를 한 번 재생 = 1회)")
    ax.set_ylabel("누적 보상 (높을수록 좋음)")
    ax.set_title("Q-learning 학습 곡선 (1번째 분할)", color=INK, loc="left")
    fig.tight_layout()
    fig.savefig(f"{OUTDIR}/C_qlearning_curve.png", dpi=150); plt.close(fig)


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    S = json.load(open(STATS, encoding="utf-8"))
    P = dict(np.load(PRED, allow_pickle=True))
    fig_roc(S, P)
    fig_rsrp(S)
    if "C" in S:
        fig_policy(S)
        fig_qcurve(S)
    print(f"→ {OUTDIR}/")


if __name__ == "__main__":
    main()
