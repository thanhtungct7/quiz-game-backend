from scripts.export_vocab_images import asset_path


def test_a_stored_picture_is_bundled_as_webp_under_the_same_name() -> None:
    assert asset_path("vocab/images/01_0001.jpg") == "vocab/images/01_0001.webp"
    assert asset_path("vocab/images/mountain_lion_1397924728921.jpg") == (
        "vocab/images/mountain_lion_1397924728921.webp"
    )


def test_only_pictures_are_bundled() -> None:
    assert asset_path("vocab/audio/01_0001.mp3") is None
    assert asset_path("quiz-bank-cover.svg") is None
