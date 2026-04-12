from backend.patterns import learn_pattern, match_pattern


def test_learn_pattern_consensus():
    bits = ["0101", "0111", "0101"]
    pattern = learn_pattern(bits)
    assert pattern == {"mask": "1101", "bits": "0101"}
    assert match_pattern("0101", pattern)
    assert not match_pattern("1101", pattern)
