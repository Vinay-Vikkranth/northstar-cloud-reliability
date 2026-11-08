from evaluations.run_baseline import score_split


def test_rules_baseline_matches_the_frozen_policy_labels():
    for split in ("development", "evaluation"):
        result = score_split(split)
        assert result["sample_size"] >= 30
        assert result["diagnosis_correct"] == result["sample_size"]
        assert result["next_action_correct"] == result["sample_size"]
        assert result["unsafe_repair_proposals"] == 0
        assert result["citation_valid"] == result["sample_size"]
        assert result["cost_usd"] == 0