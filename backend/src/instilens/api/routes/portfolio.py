"""Portfolios (services/portfolio). Every route is gated on the `portfolio` plan feature (api/deps.require_plan):
a plan without it answers 402 plan_limit — the UI's "Pro ile açılır" card — while plans are enforced.

GET    /portfolios                                  the owner's portfolios
POST   /portfolios {name, market}                   create (cap: plan `portfolios`)
PATCH  /portfolios/{id} {name}                      rename
DELETE /portfolios/{id}
GET    /portfolios/{id}                             positions priced at the latest close, totals, 30-day fund moves, transactions
PUT    /portfolios/{id}/positions {symbol, quantity, avg_cost?, opened_at?, note?}   enter / change a position by hand (409 for a derived row)
DELETE /portfolios/{id}/positions/{position_id}
POST   /portfolios/{id}/transactions {symbol, side, quantity, price, traded_at, fee?, note?}   record a buy / sell (position rederived, FIFO)
DELETE /portfolios/{id}/transactions/{transaction_id}
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from instilens.api.deps import current_user, get_session, require_plan
from instilens.domain.models import Portfolio, User
from instilens.services import portfolio

router = APIRouter(prefix="/api/v1/portfolios", tags=["portfolio"], dependencies=[Depends(current_user), Depends(require_plan("portfolio"))])


class PortfolioBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    market: str = Field("TR", pattern="^(TR|US)$")


class RenameBody(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class PositionBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    quantity: float | str  # numbers or numeric strings; validated as Decimal in the service
    avg_cost: float | str | None = None
    opened_at: date | None = None
    note: str | None = Field(None, max_length=256)


class TransactionBody(BaseModel):
    symbol: str = Field(min_length=1, max_length=16)
    side: str = Field(pattern="^(BUY|SELL|buy|sell)$")
    quantity: float | str
    price: float | str
    traded_at: date
    fee: float | str | None = None
    note: str | None = Field(None, max_length=256)


def _owner(user: User) -> str:
    return str(user.id)


def _portfolio(session: Session, user: User, portfolio_id: int) -> Portfolio:
    p = portfolio.get(session, _owner(user), portfolio_id)
    if p is None:
        raise HTTPException(404, "portfolio not found")
    return p


@router.get("")
def list_portfolios(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return portfolio.list_portfolios(session, _owner(user))


@router.post("", status_code=201)
def create_portfolio(body: PortfolioBody, user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        p = portfolio.create(session, _owner(user), body.name, body.market)
    except portfolio.PortfolioError as exc:
        raise HTTPException(400, str(exc)) from exc
    return portfolio.portfolio_json(session, p)


@router.patch("/{portfolio_id}")
def rename_portfolio(portfolio_id: int, body: RenameBody, user: User = Depends(current_user), session: Session = Depends(get_session)):
    p = _portfolio(session, user, portfolio_id)
    try:
        portfolio.rename(session, p, body.name)
    except portfolio.PortfolioError as exc:
        raise HTTPException(400, str(exc)) from exc
    return portfolio.portfolio_json(session, p)


@router.delete("/{portfolio_id}", status_code=204)
def delete_portfolio(portfolio_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    portfolio.delete(session, _portfolio(session, user, portfolio_id))


@router.get("/{portfolio_id}")
def get_portfolio(portfolio_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Positions with the latest stored close (never intraday), cost, P&L and weight, the stored institutional
    scores, the funds' 30-day counts per symbol and the insiders' 90-day net (US), plus the 30-day moves on the held
    symbols and the transaction history. Reported positions and stored figures — never a recommendation."""
    return portfolio.detail(session, _portfolio(session, user, portfolio_id))


@router.put("/{portfolio_id}/positions", status_code=201)
def put_position(portfolio_id: int, body: PositionBody, user: User = Depends(current_user), session: Session = Depends(get_session)):
    p = _portfolio(session, user, portfolio_id)
    try:
        pos = portfolio.upsert_position(session, p, body.symbol, body.quantity, body.avg_cost, body.opened_at, body.note)
    except portfolio.DerivedPosition as exc:
        raise HTTPException(409, str(exc)) from exc
    except portfolio.PortfolioError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": pos.id, "symbol": body.symbol.strip().upper(), "quantity": float(pos.quantity), "avg_cost": float(pos.avg_cost) if pos.avg_cost is not None else None,
            "opened_at": pos.opened_at.isoformat() if pos.opened_at else None, "note": pos.note, "derived": False}


@router.delete("/{portfolio_id}/positions/{position_id}", status_code=204)
def delete_position(portfolio_id: int, position_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    if not portfolio.delete_position(session, _portfolio(session, user, portfolio_id), position_id):
        raise HTTPException(404, "not found")


@router.post("/{portfolio_id}/transactions", status_code=201)
def post_transaction(portfolio_id: int, body: TransactionBody, user: User = Depends(current_user), session: Session = Depends(get_session)):
    p = _portfolio(session, user, portfolio_id)
    try:
        t = portfolio.add_transaction(session, p, body.symbol, body.side, body.quantity, body.price, body.traded_at, body.fee, body.note)
    except portfolio.PortfolioError as exc:
        raise HTTPException(400, str(exc)) from exc
    return portfolio.transaction_json(t, body.symbol.strip().upper())


@router.delete("/{portfolio_id}/transactions/{transaction_id}", status_code=204)
def delete_transaction(portfolio_id: int, transaction_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    p = _portfolio(session, user, portfolio_id)
    try:
        found = portfolio.delete_transaction(session, p, transaction_id)
    except portfolio.PortfolioError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not found:
        raise HTTPException(404, "not found")
