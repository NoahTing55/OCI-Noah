from app.region_names import get_region_display_name


def test_configured_region_is_used_when_oci_metadata_is_missing():
    assert get_region_display_name(None, None, configured_region="us-phoenix-1") == "美国凤凰城"


def test_short_region_key_is_recognized():
    assert get_region_display_name(None, "PHX") == "美国凤凰城"
    assert get_region_display_name(None, "LHR") == "英国伦敦"


def test_human_region_name_is_recognized():
    assert get_region_display_name("Phoenix", "PHX") == "美国凤凰城"


def test_new_canonical_region_code_does_not_become_unrecognized():
    assert get_region_display_name(None, None, configured_region="xx-future-1") == "xx-future-1"
