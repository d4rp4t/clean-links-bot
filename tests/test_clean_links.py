from telegram import MessageEntity

from clean_links_bot import (
    clean_url,
    extract_urls,
    YOUTUBE_ALLOWED_PARAMS,
)


def test_youtube_short_link_strips_si():
    url = "https://youtu.be/ko70cExuzZM?si=yO4tqv9f73N1pCUp"
    cleaned = clean_url(url)
    assert cleaned == "https://youtu.be/ko70cExuzZM"


def test_youtube_strips_tracking_params():
    url = (
        "https://www.youtube.com/watch"
        "?v=dQw4w9WgXcQ"
        "&si=ABC123"
        "&utm_source=foo"
        "&fbclid=XYZ"
    )
    cleaned =  clean_url(url)

    # Only allowed params should remain
    assert "v=dQw4w9WgXcQ" in cleaned
    assert "si=" not in cleaned
    assert "utm_source" not in cleaned
    assert "fbclid" not in cleaned

    # No unexpected params
    parsed_query = dict(
        part.split("=", 1) for part in cleaned.split("?", 1)[1].split("&")
    )
    assert set(parsed_query.keys()).issubset(YOUTUBE_ALLOWED_PARAMS)


def test_youtube_preserves_timestamp_and_playlist():
    url = (
        "https://www.youtube.com/watch"
        "?v=dQw4w9WgXcQ"
        "&t=120"
        "&list=PL123"
        "&index=5"
    )
    cleaned = clean_url(url)

    assert "v=dQw4w9WgXcQ" in cleaned
    assert "t=120" in cleaned
    assert "list=PL123" in cleaned
    assert "index=5" in cleaned


def test_youtube_short_link_keeps_t():
    url = "https://youtu.be/dQw4w9WgXcQ?t=42&utm_source=foo"
    cleaned = clean_url(url)

    assert cleaned.startswith("https://youtu.be/dQw4w9WgXcQ")
    assert "t=42" in cleaned
    assert "utm_source" not in cleaned


def test_twitter_strips_all_params():
    url = "https://x.com/user/status/1234567890?s=20&t=ABCDEFG"
    cleaned = clean_url(url)

    # Base path should remain
    assert cleaned.startswith("https://x.com/user/status/1234567890")
    # No query string
    assert "?" not in cleaned


def test_clean_url_handles_non_supported_hosts():
    url = "https://example.com/page?foo=bar&utm_source=baz"
    cleaned = clean_url(url)

    # Non-YouTube/Twitter URLs are left unchanged
    assert cleaned == url


def test_clean_url_handles_garbage_input():
    # Should not raise
    garbage = "not a url at all"
    cleaned = clean_url(garbage)
    assert cleaned == garbage


def test_extract_urls_from_message():
    text = "Check this out: https://example.com and also https://youtu.be/dQw4w9WgXcQ?t=10"

    url1 = "https://example.com"
    url2 = "https://youtu.be/dQw4w9WgXcQ?t=10"

    entities = [
        MessageEntity(
            type=MessageEntity.URL,
            offset=text.index(url1),
            length=len(url1),
        ),
        MessageEntity(
            type=MessageEntity.URL,
            offset=text.index(url2),
            length=len(url2),
        ),
    ]

    urls = extract_urls(text, entities)
    assert len(urls) == 2
    assert urls[0][0] == url1
    assert urls[1][0] == url2


def test_extract_urls_with_text_link():
    # URL hidden behind a text link
    text = "Click here"
    ent = MessageEntity(
        type=MessageEntity.TEXT_LINK,
        offset=0,
        length=len(text),
        url="https://x.com/user/status/123?s=20",
    )
    urls = extract_urls(text, [ent])

    assert len(urls) == 1
    assert urls[0][0] == "https://x.com/user/status/123?s=20"
