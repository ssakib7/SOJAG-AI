"""Pin the deterministic backstops to the old bot's exact behaviour."""

from app.utils.text import (
    find_phone,
    find_trx_id,
    looks_like_payment_claim,
    normalize_phone,
    real_name,
    split_for_messenger,
)


class TestNormalizePhone:
    def test_plain(self):
        assert normalize_phone("01712345678") == "01712345678"

    def test_country_code(self):
        assert normalize_phone("+8801712345678") == "01712345678"
        assert normalize_phone("8801712345678") == "01712345678"

    def test_bengali_digits(self):
        assert normalize_phone("০১৭১২৩৪৫৬৭৮") == "01712345678"

    def test_spaces_dashes(self):
        assert normalize_phone("017 1234-5678") == "01712345678"

    def test_invalid(self):
        assert normalize_phone("0171234567") is None  # 10 digits
        assert normalize_phone("02712345678") is None  # not 01x
        assert normalize_phone("hello") is None


class TestFindPhone:
    def test_embedded(self):
        assert find_phone("James 01712345678") == "01712345678"

    def test_bengali_with_punctuation(self):
        assert find_phone("আমার নম্বর ০১৭১২৩৪৫৬৭৮।") == "01712345678"

    def test_country_code_embedded(self):
        assert find_phone("call +88 017-1234 5678 pls") == "01712345678"

    def test_no_partial_of_longer_number(self):
        assert find_phone("id 2017123456789") is None

    def test_none(self):
        assert find_phone("dam koto") is None


class TestTrxId:
    def test_mixed_alnum(self):
        assert find_trx_id("TrxID 8N7A2B3C4D") == "8N7A2B3C4D"

    def test_urls_stripped(self):
        # Share-link short codes look exactly like trx ids.
        assert find_trx_id("dekhen https://vm.tiktok.com/ZSVhEN6k5/") is None
        assert find_trx_id("https://youtu.be/dQw4w9WgXcQ") is None

    def test_plain_words_and_numbers_rejected(self):
        assert find_trx_id("payment koresi vai") is None
        assert find_trx_id("01712345678") is None  # digits only — a phone, not a trx id


class TestPaymentClaim:
    def test_question_is_not_claim(self):
        assert not looks_like_payment_claim("course fee ki eksathe payment korte hoi")
        assert not looks_like_payment_claim("বিকাশে কি ফি দেওয়া যায়?")

    def test_claims(self):
        assert looks_like_payment_claim("টাকা পাঠিয়ে দিয়েছি")
        assert looks_like_payment_claim("bkash e payment korechi")
        assert looks_like_payment_claim("just paid the fee")
        assert looks_like_payment_claim("8N7A2B3C4D")  # bare trx id

    def test_promise_is_not_claim(self):
        assert not looks_like_payment_claim("kal bkash korbo")

    def test_empty(self):
        assert not looks_like_payment_claim("")
        assert not looks_like_payment_claim(None)


class TestRealName:
    def test_placeholder_rejected(self):
        assert real_name("(নাম জানা যায়নি)") is None

    def test_real(self):
        assert real_name("  Sakib  ") == "Sakib"

    def test_empty(self):
        assert real_name("") is None
        assert real_name(None) is None


class TestSplit:
    def test_short_untouched(self):
        assert split_for_messenger("hello") == ["hello"]

    def test_splits_on_blank_line(self):
        text = ("ক" * 1500) + "\n\n" + ("খ" * 1500)
        chunks = split_for_messenger(text)
        assert len(chunks) == 2
        assert chunks[0] == "ক" * 1500
        assert chunks[1] == "খ" * 1500

    def test_splits_on_bangla_sentence(self):
        text = ("ক" * 1500) + "। " + ("খ" * 1500)
        chunks = split_for_messenger(text)
        assert len(chunks) == 2
        assert all(len(c) <= 2000 for c in chunks)

    def test_unbroken_token_hard_cut(self):
        chunks = split_for_messenger("x" * 4100)
        assert all(len(c) <= 2000 for c in chunks)
        assert "".join(chunks) == "x" * 4100
