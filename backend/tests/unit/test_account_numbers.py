import pytest

from app.modules.account_numbers.domain.numbers import (
    RANDOM_SPACE,
    AccountNumber,
    AccountType,
    InvalidAccountNumber,
    candidate,
    luhn_check_digit,
)


def test_the_documented_example_is_valid() -> None:
    number = AccountNumber("1001004827")
    assert number.account_type is AccountType.PERSONAL
    assert number.display == "1001 004 827"
    assert str(number) == "1001004827"


@pytest.mark.parametrize(
    ("payload", "digit"),
    [("7992739871", "3"), ("100100482", "7"), ("0", "0"), ("411111111111111", "1")],
)
def test_luhn_check_digits_match_known_values(payload: str, digit: str) -> None:
    assert luhn_check_digit(payload) == digit


@pytest.mark.parametrize("payload", ["", "12a", "12 3", "١٢٣", "1.5"])
def test_luhn_takes_only_ascii_digits(payload: str) -> None:
    with pytest.raises(ValueError):
        luhn_check_digit(payload)


@pytest.mark.parametrize("account_type", AccountType)
def test_each_type_has_its_prefix(account_type: AccountType) -> None:
    number = AccountNumber.compose(account_type, 42)
    assert number.value.startswith(account_type.value)
    assert number.value[2:9] == "0000042"
    assert number.account_type is account_type
    assert len(number.value) == 10


def test_prefixes_are_the_documented_ones() -> None:
    assert {member.name: member.value for member in AccountType} == {
        "PERSONAL": "10",
        "JOINT": "20",
        "COMPANY": "30",
        "GROUP": "40",
    }


def numbers_to_attack() -> list[AccountNumber]:
    return [
        AccountNumber.compose(account_type, random_part)
        for account_type in AccountType
        for random_part in (0, 1, 9, 90, 909, 1_004_827, 4_827_100, 5_555_555, 9_999_999)
    ]


def test_every_single_mistyped_digit_is_caught() -> None:
    for number in numbers_to_attack():
        for position in range(10):
            for digit in "0123456789":
                if digit == number.value[position]:
                    continue
                typo = number.value[:position] + digit + number.value[position + 1 :]
                with pytest.raises(InvalidAccountNumber):
                    AccountNumber(typo)


def test_swapped_neighbouring_digits_are_caught() -> None:
    # Luhn's one known blind spot is swapping 0 and 9, so that pair is left out.
    for number in numbers_to_attack():
        for position in range(9):
            pair = number.value[position : position + 2]
            if pair[0] == pair[1] or set(pair) == {"0", "9"}:
                continue
            typo = number.value[:position] + pair[1] + pair[0] + number.value[position + 2 :]
            with pytest.raises(InvalidAccountNumber):
                AccountNumber(typo)


@pytest.mark.parametrize(
    "value",
    [
        "100100482",  # nine digits
        "10010048270",  # eleven digits
        "1001 004 827",  # the stored form has no spaces
        "9901004828",  # unknown type
        "0001004823",
        "10010048२7",
        "",
    ],
)
def test_malformed_numbers_are_refused(value: str) -> None:
    with pytest.raises(InvalidAccountNumber):
        AccountNumber(value)


def test_an_unknown_type_is_refused_even_with_a_good_check_digit() -> None:
    payload = "500000001"
    with pytest.raises(InvalidAccountNumber):
        AccountNumber(payload + luhn_check_digit(payload))


@pytest.mark.parametrize("typed", ["1001004827", "1001 004 827", " 1001 004 827 ", "10 01004827"])
def test_parse_accepts_what_people_type(typed: str) -> None:
    assert AccountNumber.parse(typed) == AccountNumber("1001004827")


@pytest.mark.parametrize("typed", ["1001-004-827", "1001  004 827", "1001 004 82", "abc", ""])
def test_parse_refuses_everything_else(typed: str) -> None:
    with pytest.raises(InvalidAccountNumber):
        AccountNumber.parse(typed)


@pytest.mark.parametrize("random_part", [-1, RANDOM_SPACE, 1.0, True])
def test_the_random_part_is_seven_digits(random_part: object) -> None:
    with pytest.raises(ValueError):
        AccountNumber.compose(AccountType.PERSONAL, random_part)  # type: ignore[arg-type]


def test_candidate_draws_from_the_whole_seven_digit_space() -> None:
    asked: list[int] = []

    def randbelow(limit: int) -> int:
        asked.append(limit)
        return limit - 1

    number = candidate(AccountType.GROUP, randbelow)
    assert asked == [10_000_000]
    assert number.value[:9] == "409999999"
