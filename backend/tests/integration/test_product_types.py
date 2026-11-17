"""Product types against real PostgreSQL: two people define one, and products and bond issues
take only a type in use of their own model.

Types are never removed and tests share one database, so every test makes its own type with
its own code and name.
"""

import asyncio
import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

import app.api.app  # noqa: F401 - loads the modules in the order the app does, which has no cycle
from app.infrastructure.database import Database
from app.modules.bonds import catalogue as bonds
from app.modules.investments import service as investments
from app.modules.investments.domain.products import InterestPaid, ProductKind
from app.modules.product_types import service as types
from app.modules.product_types.domain.types import (
    ChangeStatus,
    NewType,
    ReturnModel,
    TypeAction,
    TypeStatus,
    TypeTerms,
)
from app.shared.actors import Actor
from app.shared.errors import ApplicationError
from app.shared.money import Currency
from tests.support.clock import AsyncBarrier, FakeClock
from tests.support.ledger import staff as new_staff

pytestmark = pytest.mark.integration

NOW = FakeClock(datetime(2026, 11, 13, 9, tzinfo=UTC))


def tag() -> str:
    return uuid.uuid4().hex[:8].upper()


def refused(error: pytest.ExceptionInfo[ApplicationError]) -> tuple[str, list[tuple[str, str]]]:
    return error.value.code, [(str(p.field_path[0]), p.code) for p in error.value.fields]


async def propose(
    database: Database, staff: Actor, code: str, model: ReturnModel, name: str
) -> types.ChangeView:
    new = NewType(code, model, TypeTerms(name, "Issued by a listed company.", 1500))
    async with database.actor_transaction(staff) as session:
        return await types.propose_type(session, staff, new, "Clients asked for it", NOW)


async def approve(database: Database, staff: Actor, change_id: str) -> types.ChangeView:
    async with database.actor_transaction(staff) as session:
        return await types.approve(session, staff, change_id, NOW)


async def defined(database: Database, model: ReturnModel) -> str:
    """A type two people made. Its code."""
    code = f"TYPE-{tag()}"
    change = await propose(database, new_staff(), code, model, f"Type {code}")
    await approve(database, new_staff(), change.id)
    return code


async def get(database: Database, code: str) -> types.TypeView:
    async with database.actor_transaction(new_staff()) as session:
        return await types.get_type(session, code)


def fixed_income(code: str, type_code: str | None) -> investments.NewProduct:
    return investments.NewProduct(
        code=code,
        name=f"Deposit {code}",
        kind=ProductKind.FIXED_INCOME,
        currency=Currency.UGX,
        term_days=365,
        minimum_minor=500_000,
        interest_paid=InterestPaid.MONTHLY,
        annual_rate_bps=1400,
        tax_bps=1500,
        type_code=type_code,
    )


async def propose_product(
    database: Database, product: investments.NewProduct
) -> investments.ProductChangeView:
    staff = new_staff()
    async with database.actor_transaction(staff) as session:
        return await investments.propose_new_product(session, staff, product, "New", NOW)


async def type_of_product(database: Database, code: str) -> str:
    async with database.actor_transaction(new_staff()) as session:
        row = (
            await session.execute(
                text(
                    "SELECT t.code FROM investment_products p JOIN product_types t "
                    "ON t.id = p.type_id WHERE p.code = :code"
                ),
                {"code": code},
            )
        ).one()
    return str(row.code)


async def test_the_three_types_the_system_started_with_are_built_in(
    app_database: Database,
) -> None:
    async with app_database.actor_transaction(new_staff()) as session:
        found = {t.code: t for t in await types.list_types(session)}
    assert {code: (t.model, t.built_in) for code, t in found.items() if t.built_in} == {
        "TREASURY-BILL": (ReturnModel.TREASURY_BILL, True),
        "FIXED-INCOME": (ReturnModel.FIXED_INCOME, True),
        "TREASURY-BOND": (ReturnModel.TREASURY_BOND, True),
    }
    # Bank of Uganda's withholding: 20% on bills and bonds. Fixed income keeps 15% (0530).
    assert {code: t.terms.default_tax_bps for code, t in found.items() if t.built_in} == {
        "TREASURY-BILL": 2000,
        "FIXED-INCOME": 1500,
        "TREASURY-BOND": 2000,
    }
    # The seeded products have their kind's type.
    assert await type_of_product(app_database, "TREA-91") == "TREASURY-BILL"


async def test_two_people_define_a_type_and_products_of_it_take_it(
    app_database: Database,
) -> None:
    maker, checker = new_staff(), new_staff()
    code = f"DEPOSIT-{tag()}"
    change = await propose(app_database, maker, code, ReturnModel.FIXED_INCOME, f"Fixed {code}")
    assert (change.action, change.status) == (TypeAction.CREATE, ChangeStatus.PROPOSED)
    with pytest.raises(ApplicationError) as own:
        await approve(app_database, maker, change.id)
    assert own.value.code == "forbidden"
    decided = await approve(app_database, checker, change.id)
    assert decided.status is ChangeStatus.APPROVED
    made = await get(app_database, code)
    assert (made.model, made.status, made.built_in, made.in_use) == (
        ReturnModel.FIXED_INCOME,
        TypeStatus.ACTIVE,
        False,
        0,
    )

    product_code = f"FD-{tag()}"
    proposed = await propose_product(app_database, fixed_income(product_code, code))
    async with app_database.actor_transaction(checker) as session:
        await investments.approve_product_change(session, checker, proposed.id, NOW)
    assert await type_of_product(app_database, product_code) == code
    assert (await get(app_database, code)).in_use == 1

    # A product with no type named takes its kind's built-in type.
    plain = f"FD-{tag()}"
    proposed = await propose_product(app_database, fixed_income(plain, None))
    async with app_database.actor_transaction(checker) as session:
        await investments.approve_product_change(session, checker, proposed.id, NOW)
    assert await type_of_product(app_database, plain) == "FIXED-INCOME"


async def test_a_product_or_issue_takes_only_a_type_of_its_own_model_in_use(
    app_database: Database,
) -> None:
    bond_type = await defined(app_database, ReturnModel.TREASURY_BOND)
    with pytest.raises(ApplicationError) as wrong:
        await propose_product(app_database, fixed_income(f"FD-{tag()}", bond_type))
    assert refused(wrong) == ("validation_failed", [("type_code", "wrong_model")])
    with pytest.raises(ApplicationError) as unknown:
        await propose_product(app_database, fixed_income(f"FD-{tag()}", "NO-SUCH-TYPE"))
    assert refused(unknown) == ("validation_failed", [("type_code", "unknown_type")])

    # A corporate bond is a bond issue of its own type.
    issue_code = f"CORP-{tag()}"
    maker, checker = new_staff(), new_staff()
    async with app_database.actor_transaction(maker) as session:
        change = await bonds.propose_issue(
            session,
            maker,
            bonds.NewIssue(
                code=issue_code,
                isin=f"UG{tag()}A1",
                name=f"Corporate bond {issue_code}",
                currency=Currency.UGX,
                coupon_bps=1700,
                tax_bps=1500,
                issue_date=date(2025, 6, 1),
                maturity_date=date(2030, 6, 1),
                minimum_face_minor=100_000,
                face_step_minor=100_000,
                price=1_000_000,
                type_code=bond_type,
            ),
            "Listed on the exchange",
            NOW,
        )
    async with app_database.actor_transaction(checker) as session:
        await bonds.approve(session, checker, change.id, NOW)
    assert (await get(app_database, bond_type)).in_use == 1

    # Retired, the type takes no new products. What exists carries on.
    maker, checker = new_staff(), new_staff()
    async with app_database.actor_transaction(maker) as session:
        retire = await types.propose_status(
            session, maker, bond_type, TypeAction.RETIRE, "No more of these", NOW
        )
    await approve(app_database, checker, retire.id)
    assert (await get(app_database, bond_type)).status is TypeStatus.RETIRED
    with pytest.raises(ApplicationError) as retired:
        async with app_database.actor_transaction(maker) as session:
            await bonds.propose_issue(
                session,
                maker,
                bonds.NewIssue(
                    code=f"CORP-{tag()}",
                    isin=f"UG{tag()}A2",
                    name=f"Corporate bond {tag()}",
                    currency=Currency.UGX,
                    coupon_bps=1700,
                    tax_bps=1500,
                    issue_date=date(2025, 6, 1),
                    maturity_date=date(2030, 6, 1),
                    minimum_face_minor=100_000,
                    face_step_minor=100_000,
                    price=1_000_000,
                    type_code=bond_type,
                ),
                "Another",
                NOW,
            )
    assert refused(retired) == ("validation_failed", [("type_code", "type_retired")])


async def test_an_edit_changes_the_name_and_usual_tax_but_never_the_model(
    app_database: Database,
) -> None:
    code = await defined(app_database, ReturnModel.TREASURY_BILL)
    maker, checker = new_staff(), new_staff()
    terms = TypeTerms(f"Bill {code} renamed", "Ninety one days at the auction.", 2000)
    async with app_database.actor_transaction(maker) as session:
        change = await types.propose_edit(session, maker, code, terms, "Clearer name", NOW)
        with pytest.raises(ApplicationError) as waiting:
            await types.propose_status(session, maker, code, TypeAction.RETIRE, "Also", NOW)
    assert waiting.value.code == "conflict"
    await approve(app_database, checker, change.id)
    edited = await get(app_database, code)
    assert (edited.terms, edited.model, edited.version) == (terms, ReturnModel.TREASURY_BILL, 2)

    # A name another type has is refused, whatever its case.
    async with app_database.actor_transaction(maker) as session:
        with pytest.raises(ApplicationError) as taken:
            await types.propose_edit(
                session,
                maker,
                code,
                TypeTerms("treasury bill", "Same as the built-in.", 1500),
                "Clash",
                NOW,
            )
    assert refused(taken) == ("validation_failed", [("name", "name_taken")])


async def test_two_checkers_approving_at_once_write_the_type_once(
    app_database: Database,
) -> None:
    code = f"TYPE-{tag()}"
    change = await propose(app_database, new_staff(), code, ReturnModel.FIXED_INCOME, code)
    barrier = AsyncBarrier(2)

    async def decide() -> str:
        checker = new_staff()
        try:
            async with app_database.actor_transaction(checker) as session:
                await barrier.wait()
                await types.approve(session, checker, change.id, NOW)
        except ApplicationError as error:
            return error.code
        return "approved"

    assert sorted(await asyncio.gather(decide(), decide())) == ["approved", "conflict"]
    assert (await get(app_database, code)).version == 1


async def test_the_database_writes_a_type_only_from_an_approval(app_database: Database) -> None:
    staff = new_staff()
    with pytest.raises(DBAPIError, match="only by an approved change"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "INSERT INTO product_types (id, code, name, model, description, "
                    "default_tax_bps, status, version, change_id, created_at, updated_at) "
                    "VALUES (:id, :code, :code, 'fixed_income', 'Sneaked in', 0, 'active', 1, "
                    ":id, now(), now())"
                ),
                {"id": f"-{tag()}{tag()}000", "code": f"SNEAK-{tag()}"},
            )
    with pytest.raises(DBAPIError, match=r"permission denied|never changes|one approved change"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text("UPDATE product_types SET model = 'treasury_bond' WHERE code = 'FIXED-INCOME'")
            )
    with pytest.raises(DBAPIError, match=r"permission denied|never removed"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(text("DELETE FROM product_types WHERE code = 'FIXED-INCOME'"))
    # A product change whose type is not of its kind is refused.
    with pytest.raises(DBAPIError, match="of its own kind"):
        async with app_database.actor_transaction(staff) as session:
            await session.execute(
                text(
                    "INSERT INTO investment_product_changes (id, action, code, name, kind, "
                    "currency, term_days, minimum_minor, interest_paid, product_status, "
                    "first_annual_rate_bps, first_tax_bps, reason, proposed_by, proposed_at, "
                    "type_id) VALUES (:id, 'create', :code, 'Mismatch', 'fixed_income', 'UGX', "
                    "365, 500000, 'at_maturity', 'draft', 1400, 1500, 'Test', :staff, now(), "
                    "'-PTYPE0000000000000T')"
                ),
                {"id": f"-{tag()}{tag()}000", "code": f"MIS-{tag()}", "staff": staff.id},
            )


async def test_a_change_from_the_release_before_takes_the_built_in_type(
    app_database: Database,
) -> None:
    # While a deployment runs, the release before this one saves changes naming no type.
    staff = new_staff()
    change_id = f"-{tag()}{tag()}000"
    async with app_database.actor_transaction(staff) as session:
        await session.execute(
            text(
                "INSERT INTO investment_product_changes (id, action, code, name, kind, "
                "currency, term_days, minimum_minor, interest_paid, product_status, "
                "first_annual_rate_bps, first_tax_bps, reason, proposed_by, proposed_at) "
                "VALUES (:id, 'create', :code, 'Old release', 'fixed_income', 'UGX', 365, "
                "500000, 'at_maturity', 'draft', 1400, 1500, 'Test', :staff, now())"
            ),
            {"id": change_id, "code": f"OLD-{tag()}", "staff": staff.id},
        )
        found = await session.execute(
            text(
                "SELECT t.code FROM investment_product_changes c JOIN product_types t "
                "ON t.id = c.type_id WHERE c.id = :id"
            ),
            {"id": change_id},
        )
        assert found.scalar_one() == "FIXED-INCOME"


async def test_a_bond_issue_saved_without_a_type_is_given_the_type_its_change_names(
    migrator_database: Database,
) -> None:
    """The release before types names none. The type check fills one in for a new bond issue,
    from its change, without reading a column only bond changes have (fixed in 0494)."""
    tag = uuid.uuid4().hex[:8].upper()
    ids = {
        "change": f"-TypeFill{tag}Change"[:20].ljust(20, "0"),
        "issue": f"-TypeFill{tag}Issue0"[:20].ljust(20, "0"),
        "code": f"BOND-{tag}",
        "isin": f"UGF{tag}0",
        "staff": new_staff().id,
    }
    async with migrator_database.transaction() as session:
        await session.execute(
            text(
                "INSERT INTO bond_changes (id, action, code, isin, name, currency, coupon_bps, "
                "tax_bps, issue_date, maturity_date, minimum_face_minor, face_step_minor, price, "
                "reason, proposed_by, proposed_at) VALUES (:change, 'create', :code, :isin, "
                "'Test bond', 'UGX', 1600, 1000, '2025-05-14', '2037-05-14', 100000, 100000, "
                "985000, 'From the auction', :staff, now())"
            ),
            ids,
        )
        await session.execute(
            text(
                "INSERT INTO bond_issues (id, code, isin, name, currency, coupon_bps, tax_bps, "
                "issue_date, maturity_date, minimum_face_minor, face_step_minor, status, version, "
                "change_id, created_at, updated_at) VALUES (:issue, :code, :isin, 'Test bond', "
                "'UGX', 1600, 1000, '2025-05-14', '2037-05-14', 100000, 100000, 'draft', 1, "
                ":change, now(), now())"
            ),
            ids,
        )
        filled = (
            await session.execute(
                text(
                    "SELECT c.type_id AS change_type, i.type_id AS issue_type FROM bond_issues i "
                    "JOIN bond_changes c ON c.id = i.change_id WHERE i.id = :issue"
                ),
                ids,
            )
        ).one()
    assert (filled.change_type, filled.issue_type) == (
        "-PTYPE0000000000000T",
        "-PTYPE0000000000000T",
    )
