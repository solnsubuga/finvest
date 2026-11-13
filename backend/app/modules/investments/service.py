"""What the investments module offers the rest of the backend.

Other modules import from here and never from the files behind it. The catalogue and rate
commands are in `catalogue.py`, and investments themselves are in `holdings.py`.

Functions and types whose names start with `client_` or `Client` carry only figures after tax.
They are the only ones a client-facing route may use.
"""

from app.modules.investments.accrual import InterestRun, accrue_investment, interest_runs
from app.modules.investments.catalogue import (
    MAX_PROPOSALS,
    PROPOSAL_RESOURCE,
    ClientProduct,
    ClientQuote,
    ProductView,
    ProposalView,
    approve_rate,
    client_products,
    client_quote,
    get_product,
    list_products,
    list_proposals,
    propose_rate,
    rate_history,
    send_back_rate,
    withdraw_rate,
)
from app.modules.investments.holdings import (
    REQUEST_OPERATION,
    STARTED_TOPIC,
    ClientInvestment,
    Investment,
    PayableInvestment,
    cancel_unpaid,
    client_investment,
    client_investments,
    get_investment,
    list_investments,
    payable_investment,
    request_investment,
    start_investment,
)
from app.modules.investments.maturity import (
    MATURED_TOPIC,
    ROLLED_OVER_TOPIC,
    choose_at_maturity,
    mature_investment,
    pay_out_matured,
)

__all__ = [
    "MATURED_TOPIC",
    "MAX_PROPOSALS",
    "PROPOSAL_RESOURCE",
    "REQUEST_OPERATION",
    "ROLLED_OVER_TOPIC",
    "STARTED_TOPIC",
    "ClientInvestment",
    "ClientProduct",
    "ClientQuote",
    "InterestRun",
    "Investment",
    "PayableInvestment",
    "ProductView",
    "ProposalView",
    "accrue_investment",
    "approve_rate",
    "cancel_unpaid",
    "choose_at_maturity",
    "client_investment",
    "client_investments",
    "client_products",
    "client_quote",
    "get_investment",
    "get_product",
    "interest_runs",
    "list_investments",
    "list_products",
    "list_proposals",
    "mature_investment",
    "pay_out_matured",
    "payable_investment",
    "propose_rate",
    "rate_history",
    "request_investment",
    "send_back_rate",
    "start_investment",
    "withdraw_rate",
]
