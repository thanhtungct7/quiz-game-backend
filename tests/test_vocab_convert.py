from scripts.convert import content_type_of, hide_word, mask, media_items, storage_path


def test_images_and_audio_go_to_their_own_folders() -> None:
    assert storage_path("01_0001.jpg") == "vocab/images/01_0001.jpg"
    assert storage_path("01_0001_meaning.mp3") == "vocab/audio/01_0001_meaning.mp3"
    assert storage_path("hair1.wav") == "vocab/audio/hair1.wav"


def test_a_space_in_an_extra_deck_name_becomes_an_underscore() -> None:
    assert storage_path("mountain lion_1397924728921.jpg") == (
        "vocab/images/mountain_lion_1397924728921.jpg"
    )


def test_content_type_follows_the_extension() -> None:
    assert content_type_of("vocab/images/01_0001.jpg") == "image/jpeg"
    assert content_type_of("vocab/audio/01_0001.mp3") == "audio/mpeg"
    assert content_type_of("vocab/audio/gray.ogg") == "audio/ogg"
    assert content_type_of("vocab/audio/hair1.wav") == "audio/wav"


def test_the_definition_no_longer_gives_the_word_away() -> None:
    masked, answer = mask("To <i>agree</i> is to have the same opinion.", "i")
    assert masked == "To ___ is to have the same opinion."
    assert answer == "agree"


def test_the_example_keeps_the_inflected_form_as_its_answer() -> None:
    masked, answer = mask("They <b>arrived</b> at school at 7 a.m.", "b")
    assert masked == "They ___ at school at 7 a.m."
    assert answer == "arrived"


def test_a_phrasal_verb_split_in_two_is_blanked_twice() -> None:
    masked, answer = mask("When you <i>figure</i> something <i>out</i>, you understand it.", "i")
    assert masked == "When you ___ something ___, you understand it."
    assert answer == "figure out"


def test_text_without_the_tag_cannot_be_masked() -> None:
    assert mask("ˌækəˈdemɪk", "b") == (None, None)


def test_the_word_is_hidden_where_the_italics_missed_it() -> None:
    masked, _ = mask("To <i>ride</i> something is to travel on it. You can ride an animal.", "i")
    assert hide_word(masked, "ride") == "To ___ something is to travel on it. You can ___ an animal."


def test_hiding_a_word_leaves_longer_words_that_contain_it() -> None:
    assert hide_word("A ___ rides a horse to the riverside.", "ride") == (
        "A ___ rides a horse to the riverside."
    )


def test_media_items_include_the_extra_recordings() -> None:
    entry = {
        "image": {"path": "vocab/images/a.jpg", "anki_file": "1"},
        "audio_word": {"path": "vocab/audio/a.mp3", "anki_file": "2"},
        "audio_word_extra": [{"path": "vocab/audio/a2.mp3", "anki_file": "3"}],
        "audio_meaning": None,
        "audio_example": None,
    }
    assert [item["anki_file"] for item in media_items(entry)] == ["1", "2", "3"]
