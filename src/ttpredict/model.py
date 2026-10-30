"""Train and evaluate outcome models with a strict temporal split.

* Split: whole tournaments. Train = the first nine events (Jan 6 – Mar 3),
  test = the last four (Mar 10 – Mar 31). The test set is touched exactly once,
  after model selection.
* Model selection: expanding-window cross-validation inside the training
  period — for each of the last four training tournaments, fit on all earlier
  tournaments and score on that one. Hyper-parameters are chosen by mean log
  loss.
* Models: class prior, a rating-only logistic regression (the "Elo baseline"),
  a multinomial logistic regression on all features, and a gradient-boosted
  tree ensemble.
* Primary metric: log loss (a proper scoring rule, sensitive to calibration,
  which matters for a three-way outcome with a rare class). Accuracy, macro-F1,
  Brier score and one-vs-rest ROC AUC are reported alongside, with 95%
  bootstrap intervals and paired differences against the Elo baseline.

    python -m ttpredict.model
"""

from __future__ import annotations

import itertools
import json
import warnings

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.base import clone  # noqa: E402
from sklearn.calibration import calibration_curve  # noqa: E402
from sklearn.dummy import DummyClassifier  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import accuracy_score, confusion_matrix, f1_score, log_loss, roc_auc_score  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from .config import CLASSES, RANDOM_STATE, REPORTS_DIR, TEST_TOURNAMENTS, TRAIN_TOURNAMENTS  # noqa: E402
from .features import FEATURES_CSV, FEATURE_COLUMNS  # noqa: E402

ELO_COLUMNS = ["rating_diff", "abs_rating_diff"]
N_CV_FOLDS = 4
N_BOOTSTRAP = 1000
LABELS = np.arange(len(CLASSES))


# --------------------------------------------------------------------------- data


def load_dataset() -> tuple[pd.DataFrame, pd.DataFrame]:
    df = pd.read_csv(FEATURES_CSV, dtype={"game_id": str}, keep_default_na=False, na_values=[""])
    df["exclude_reason"] = df["exclude_reason"].fillna("")
    df = df[df["exclude_reason"] == ""].copy()
    df["y"] = df["outcome"].map({c: i for i, c in enumerate(CLASSES)}).astype(int)
    assert df[FEATURE_COLUMNS].notna().all().all(), "features must be complete after exclusions"
    train = df[df["tournament"].isin(TRAIN_TOURNAMENTS)].reset_index(drop=True)
    test = df[df["tournament"].isin(TEST_TOURNAMENTS)].reset_index(drop=True)
    assert set(train["tournament"]).isdisjoint(test["tournament"])
    assert train["start_time"].max() < test["start_time"].min(), "test must be strictly later than train"
    return train, test


# ------------------------------------------------------------------------- models


def candidate_models() -> dict[str, tuple[list[str], object, list[dict]]]:
    """name -> (feature columns, estimator, hyper-parameter grid)."""
    logreg = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000))
    gbm = HistGradientBoostingClassifier(random_state=RANDOM_STATE, early_stopping=False)
    gbm_grid = [
        dict(zip(("learning_rate", "max_iter", "max_leaf_nodes", "min_samples_leaf", "l2_regularization"), v))
        for v in itertools.product([0.01, 0.03, 0.1], [100, 300], [3, 7, 15], [50, 200], [0.0, 1.0])
    ]
    return {
        "prior": (ELO_COLUMNS, DummyClassifier(strategy="prior"), [{}]),
        "elo_logreg": (ELO_COLUMNS, clone(logreg), [{"logisticregression__C": c} for c in (0.001, 0.01, 0.1, 1.0)]),
        "logreg": (FEATURE_COLUMNS, clone(logreg), [{"logisticregression__C": c} for c in (0.001, 0.01, 0.1, 1.0, 10.0)]),
        "gbm": (FEATURE_COLUMNS, gbm, gbm_grid),
    }


def cv_folds(train: pd.DataFrame) -> list[tuple[np.ndarray, np.ndarray]]:
    """Expanding window: validate on each of the last N_CV_FOLDS training events."""
    order = [t for t in TRAIN_TOURNAMENTS if t in set(train["tournament"])]
    folds = []
    for k in range(len(order) - N_CV_FOLDS, len(order)):
        fit_idx = np.flatnonzero(train["tournament"].isin(order[:k]))
        val_idx = np.flatnonzero(train["tournament"] == order[k])
        folds.append((fit_idx, val_idx))
    return folds


def select(name, cols, estimator, grid, train, folds) -> tuple[dict, list[dict]]:
    X, y = train[cols].to_numpy(), train["y"].to_numpy()
    results = []
    for params in grid:
        losses = []
        for fit_idx, val_idx in folds:
            model = clone(estimator).set_params(**params).fit(X[fit_idx], y[fit_idx])
            losses.append(log_loss(y[val_idx], model.predict_proba(X[val_idx]), labels=LABELS))
        results.append({"params": params, "cv_log_loss": float(np.mean(losses)), "cv_log_loss_std": float(np.std(losses))})
    best = min(results, key=lambda r: r["cv_log_loss"])
    print(f"{name:>11}: best CV log loss {best['cv_log_loss']:.4f} with {best['params']}")
    return best, results


# ------------------------------------------------------------------------ metrics


def brier(y: np.ndarray, p: np.ndarray) -> float:
    onehot = np.eye(len(CLASSES))[y]
    return float(np.mean(np.sum((p - onehot) ** 2, axis=1)))


def metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    pred = p.argmax(axis=1)
    return {
        "log_loss": float(log_loss(y, p, labels=LABELS)),
        "brier": brier(y, p),
        "accuracy": float(accuracy_score(y, pred)),
        "macro_f1": float(f1_score(y, pred, labels=LABELS, average="macro", zero_division=0)),
        "roc_auc_ovr": float(roc_auc_score(y, p, multi_class="ovr", labels=LABELS)),
    }


def bootstrap(y, probs: dict[str, np.ndarray], baseline: str, rng) -> dict[str, dict]:
    """95% percentile intervals per model, and paired differences vs `baseline`."""
    n = len(y)
    draws = {name: {k: [] for k in ("log_loss", "accuracy", "d_log_loss", "d_accuracy")} for name in probs}
    for _ in range(N_BOOTSTRAP):
        idx = rng.integers(0, n, n)
        yb = y[idx]
        base_ll = log_loss(yb, probs[baseline][idx], labels=LABELS)
        base_acc = accuracy_score(yb, probs[baseline][idx].argmax(1))
        for name, p in probs.items():
            ll = log_loss(yb, p[idx], labels=LABELS)
            acc = accuracy_score(yb, p[idx].argmax(1))
            draws[name]["log_loss"].append(ll)
            draws[name]["accuracy"].append(acc)
            draws[name]["d_log_loss"].append(ll - base_ll)
            draws[name]["d_accuracy"].append(acc - base_acc)
    return {
        name: {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in d.items()}
        for name, d in draws.items()
    }


# -------------------------------------------------------------------------- plots


def plot_calibration(y, p, path) -> None:
    fig, axes = plt.subplots(1, 3, figsize=(12, 4), sharey=True)
    for k, (ax, cls) in enumerate(zip(axes, CLASSES)):
        frac, mean = calibration_curve(y == k, p[:, k], n_bins=10, strategy="quantile")
        ax.plot([0, 1], [0, 1], ls="--", c="grey", lw=1)
        ax.plot(mean, frac, marker="o")
        ax.set_title(cls.replace("_", " "))
        ax.set_xlabel("predicted probability")
        lim = max(p[:, k].max(), frac.max()) * 1.05
        ax.set_xlim(0, lim)
        ax.set_ylim(0, lim)
    axes[0].set_ylabel("observed frequency")
    fig.suptitle("Calibration on the test set (deciles of predicted probability)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_confusion(y, p, path) -> None:
    cm = confusion_matrix(y, p.argmax(1), labels=LABELS)
    fig, ax = plt.subplots(figsize=(4.5, 4))
    ax.imshow(cm, cmap="Blues")
    for (i, j), v in np.ndenumerate(cm):
        ax.text(j, i, str(v), ha="center", va="center", color="white" if v > cm.max() / 2 else "black")
    names = [c.replace("_", " ") for c in CLASSES]
    ax.set_xticks(LABELS, names)
    ax.set_yticks(LABELS, names)
    ax.set_xlabel("predicted")
    ax.set_ylabel("actual")
    ax.set_title("Confusion matrix (test)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_importance(imp: pd.Series, path) -> None:
    fig, ax = plt.subplots(figsize=(6, 5))
    imp.sort_values().plot.barh(ax=ax)
    ax.set_xlabel("increase in test log loss when permuted")
    ax.set_title("Permutation importance (best model)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


# --------------------------------------------------------------------------- main


def leaky_demo(train: pd.DataFrame, test: pd.DataFrame) -> dict[str, float]:
    """What happens if you (wrongly) use the API's per-game ratings."""
    cols = ["leaky_diff", "leaky_abs_diff"]
    for d in (train, test):
        d["leaky_diff"] = d["white_rating_post"] - d["black_rating_post"]
        d["leaky_abs_diff"] = d["leaky_diff"].abs()
    model = make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)).fit(train[cols], train["y"])
    return metrics(test["y"].to_numpy(), model.predict_proba(test[cols]))


def run() -> dict:
    warnings.filterwarnings("ignore", category=FutureWarning)
    REPORTS_DIR.mkdir(exist_ok=True)
    rng = np.random.default_rng(RANDOM_STATE)
    train, test = load_dataset()
    folds = cv_folds(train)
    print(f"train: {len(train)} games / {train['tournament'].nunique()} events; "
          f"test: {len(test)} games / {test['tournament'].nunique()} events")

    y_test = test["y"].to_numpy()
    report: dict = {"n_train": len(train), "n_test": len(test), "models": {}, "cv": {}}
    probs, fitted = {}, {}
    for name, (cols, estimator, grid) in candidate_models().items():
        best, results = select(name, cols, estimator, grid, train, folds)
        model = clone(estimator).set_params(**best["params"]).fit(train[cols], train["y"])
        probs[name] = model.predict_proba(test[cols])
        fitted[name] = (cols, model)
        report["cv"][name] = {"best": best, "grid": results}
        report["models"][name] = {"test": metrics(y_test, probs[name]), "params": best["params"]}

    # The final model is chosen on CV log loss only, never on test results.
    best_name = min(("logreg", "gbm"), key=lambda n: report["cv"][n]["best"]["cv_log_loss"])
    report["best_model"] = best_name

    for name, ci in bootstrap(y_test, probs, "elo_logreg", rng).items():
        report["models"][name]["ci95"] = ci

    # Diagnostics (not used for any decision).
    report["class_balance"] = {
        "train": train["outcome"].value_counts(normalize=True).reindex(CLASSES).round(4).to_dict(),
        "test": test["outcome"].value_counts(normalize=True).reindex(CLASSES).round(4).to_dict(),
    }
    played = test["n_plies"] > 2  # no-shows lose on time without a move; post-game filter
    report["test_excluding_no_shows"] = {
        name: metrics(y_test[played], p[played]) for name, p in probs.items() if name != "prior"
    }
    report["per_tournament_log_loss"] = {
        t: {name: float(log_loss(y_test[m], probs[name][m], labels=LABELS)) for name in ("elo_logreg", best_name)}
        for t in TEST_TOURNAMENTS
        if (m := (test["tournament"] == t).to_numpy()).any()
    }
    report["leaky_post_game_rating_model"] = leaky_demo(train.copy(), test.copy())

    cols, model = fitted[best_name]
    imp = permutation_importance(
        model, test[cols], y_test, scoring="neg_log_loss", n_repeats=10, random_state=RANDOM_STATE
    )
    importance = pd.Series(imp.importances_mean, index=cols)
    report["permutation_importance"] = importance.sort_values(ascending=False).round(5).to_dict()
    lr = fitted["logreg"][1][-1]
    report["logreg_coefficients"] = {
        cls: dict(zip(FEATURE_COLUMNS, np.round(lr.coef_[k], 4).tolist())) for k, cls in enumerate(CLASSES)
    }

    plot_calibration(y_test, probs[best_name], REPORTS_DIR / "calibration.png")
    plot_confusion(y_test, probs[best_name], REPORTS_DIR / "confusion_matrix.png")
    plot_importance(importance, REPORTS_DIR / "permutation_importance.png")
    (REPORTS_DIR / "metrics.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    write_markdown(report)
    return report


def write_markdown(r: dict) -> None:
    def fmt_ci(ci):
        return f"[{ci[0]:.3f}, {ci[1]:.3f}]"

    lines = [
        "# Results (auto-generated by `python -m ttpredict.model`)",
        "",
        f"Train: {r['n_train']} games, test: {r['n_test']} games. Best model by CV log loss: **{r['best_model']}**.",
        "",
        "## Test set",
        "",
        "| model | log loss (95% CI) | Δ log loss vs Elo (95% CI) | accuracy (95% CI) | macro-F1 | Brier | ROC AUC (ovr) |",
        "|---|---|---|---|---|---|---|",
    ]
    base_ll = r["models"]["elo_logreg"]["test"]["log_loss"]
    for name, m in r["models"].items():
        t, ci = m["test"], m["ci95"]
        lines.append(
            f"| {name} | {t['log_loss']:.4f} {fmt_ci(ci['log_loss'])} | {t['log_loss'] - base_ll:+.4f} "
            f"{fmt_ci(ci['d_log_loss'])} | {t['accuracy']:.3f} {fmt_ci(ci['accuracy'])} | {t['macro_f1']:.3f} "
            f"| {t['brier']:.4f} | {t['roc_auc_ovr']:.3f} |"
        )
    lines += [
        "",
        "Δ column: model minus Elo baseline on the same test games, with a paired bootstrap 95% "
        f"interval ({N_BOOTSTRAP} game-level resamples); negative is better.",
        "",
        "## Cross-validation (expanding window over the training events)",
        "",
        "| model | CV log loss | best params |",
        "|---|---|---|",
    ]
    for name, cv in r["cv"].items():
        lines.append(f"| {name} | {cv['best']['cv_log_loss']:.4f} ± {cv['best']['cv_log_loss_std']:.4f} | `{cv['best']['params']}` |")
    lines += [
        "",
        "## Diagnostics",
        "",
        f"* Class balance — train: {r['class_balance']['train']}, test: {r['class_balance']['test']}",
        f"* Leaky model using the API's post-game ratings (for illustration only): "
        f"log loss {r['leaky_post_game_rating_model']['log_loss']:.4f}, "
        f"accuracy {r['leaky_post_game_rating_model']['accuracy']:.3f}",
        "* Test excluding no-shows (≤ 2 plies; a post-game filter, diagnostic only): "
        + ", ".join(f"{n} log loss {m['log_loss']:.4f} / acc {m['accuracy']:.3f}" for n, m in r["test_excluding_no_shows"].items()),
        "",
        "Per test tournament log loss:",
        "",
        "| tournament | " + " | ".join(next(iter(r["per_tournament_log_loss"].values())).keys()) + " |",
        "|---|---|---|",
    ]
    for t, v in r["per_tournament_log_loss"].items():
        lines.append(f"| {t} | " + " | ".join(f"{x:.4f}" for x in v.values()) + " |")
    lines += ["", "Permutation importance (test log-loss increase):", ""]
    lines += [f"* `{k}`: {v:+.4f}" for k, v in r["permutation_importance"].items()]
    (REPORTS_DIR / "results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    run()
