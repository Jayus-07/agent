"""分类器训练卡片的版本与校准契约测试。"""

from backend.eval.metadata_baseline.train_lr import build_model_card


def test_model_card_records_calibration_and_feature_contract():
    card = build_model_card(
        golden_path="golden.jsonl",
        golden_hash="golden123",
        n_samples=20,
        n_classes=2,
        embedding_on=False,
        feature_names=["lex_legal"],
        macro_f1=0.9,
        accuracy=0.9,
        dry_run=True,
        calibrated=True,
        source_split=True,
    )

    assert card["calibration"] == "sigmoid"
    assert card["feature_names"] == ["lex_legal"]
    assert card["embedding_features_on"] is False
    assert card["dry_run"] is True
    assert card["promotable"] is False
