from app.textnorm import clean, norm_name


def test_clean_strips_all_spaces():
    assert clean("\xa0Department of X ") == "Department of X"
    assert clean("材料科學與工程學系　") == "材料科學與工程學系"


def test_clean_blank_and_x_are_none():
    assert clean("x") is None and clean(" X ") is None
    assert clean(None) is None and clean("") is None and clean("　") is None


def test_clean_keeps_numbers_as_text():
    assert clean(23) == "23"


def test_norm_name():
    assert norm_name("國立臺灣 大學") == "國立台灣大學"
    assert norm_name("資訊管理學系　") == "資訊管理學系"
