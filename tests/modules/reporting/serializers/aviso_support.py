"""A synthetic month of virtual-asset avisos modelled on SAT's ejemplo_avi.xml.

Every RFC and CURP passes the party validators (see support.py).
"""

from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal

from xolotl.modules.reporting.serializers.aviso import (
    Aviso,
    AvisoInforme,
    Client,
    Counterparty,
    Exchange,
    ForeignBankAccount,
    FundsMovement,
    IdDocument,
    LegalPerson,
    MexicanAddress,
    MexicanBankAccount,
    NaturalPerson,
    ObligatedSubject,
    Operations,
    Phone,
    PlatformAccount,
    Priority,
    Representative,
    Trade,
    Transfer,
    VirtualAsset,
)

CDMX = timezone(timedelta(hours=-6))
BTC = "1001"
USDT = "1003"

FILER = ObligatedSubject(rfc="SIN150301QZ4", platform_domain="Cripto-Plataforma")

REPRESENTATIVE = Representative(
    first_names="Patricia",
    paternal_surname="Robles",
    maternal_surname="Peña",
    birth_date=date(1978, 7, 2),
    rfc="ROPA780702K78",
    curp="ROPA780702MJCDRN01",
    document=IdDocument(kind="1", number="012208180920"),
)

COMPANY_CLIENT = Client(
    account=PlatformAccount(
        user_id="USUARIO_0123ABC",
        related_account="3333222211110000",
        clabe="012345678912345678",
        currency="1",
    ),
    person=LegalPerson(
        name="Abarrotes El Cóndor, S.A. de C.V.",
        incorporation_date=date(2018, 5, 16),
        rfc="AEC180516MX3",
        nationality="MX",
        business_activity="4611001",
        representative=REPRESENTATIVE,
    ),
    address=MexicanAddress(
        neighbourhood="Centro",
        street="Calle Hidalgo",
        exterior_number="45",
        interior_number="9A",
        postal_code="44100",
    ),
    phone=Phone(country="MX", number="3312345678", email="pagos@condor.example"),
)

OWNER = NaturalPerson(
    first_names="María José",
    paternal_surname="Gómez",
    maternal_surname="López",
    birth_date=date(1985, 3, 15),
    rfc="GOLM850315AB0",
    curp="GOLM850315HDFMPR01",
    nationality="MX",
)


def _btc(rate: str, qty: str) -> VirtualAsset:
    return VirtualAsset(code=BTC, rate_mxn=Decimal(rate), quantity=Decimal(qty))


FULL_OPERATIONS = Operations(
    purchases=(
        Trade(
            at=datetime(2027, 3, 4, 9, 41, 52, tzinfo=CDMX),
            currency="1",
            amount=Decimal(75000),
            asset=_btc("1887523.6", "0.0397346025"),
            tx_hash="43e3ca273c3966a56465484654865438979846546546acdefcc216a383605163",
        ),
    ),
    sales=(
        Trade(
            at=datetime(2027, 3, 11, 18, 28, 31, tzinfo=UTC),
            currency="1",
            amount=Decimal("70852.58"),
            asset=_btc("2023051.1", "0.035022634"),
            tx_hash="056460046C3966A564654846548654361361498484632146416216A383605163",
        ),
    ),
    exchanges=(
        Exchange(
            at=datetime(2027, 3, 15, 17, 2, 45, tzinfo=CDMX),
            sent=VirtualAsset(
                code="999999",
                description="Digital Coin",
                rate_mxn=Decimal("36421.85"),
                quantity=Decimal("1.8414369346"),
            ),
            sent_mxn=Decimal("67068.54"),
            received=_btc("1507328.7", "0.0444949665"),
            received_mxn=Decimal("67068.54"),
            tx_hash="64631325642346584A5A5A464A498646146009846001048EFCC216A383605163",
        ),
    ),
    transfers_sent=(
        Transfer(
            at=datetime(2027, 3, 20, 10, 31, 54, tzinfo=CDMX),
            amount_mxn=Decimal(61200),
            asset=VirtualAsset(
                code=USDT, rate_mxn=Decimal("6.12"), quantity=Decimal(10000)
            ),
            tx_hash="895613181964D6469D4664646CD6984794DA8412318909C7897896A383605163",
        ),
    ),
    transfers_received=(
        Transfer(
            at=datetime(2027, 3, 22, 19, 20, 36, tzinfo=CDMX),
            amount_mxn=Decimal("251843.67"),
            asset=_btc("1678957.8", "0.15"),
            tx_hash="B465B64B6AA321A321A3946546498646146009846001048EFCC216A383605163",
        ),
    ),
    withdrawals=(
        FundsMovement(
            at=datetime(2027, 3, 25, 21, 14, 2, tzinfo=CDMX),
            instrument="6",
            currency="1",
            amount=Decimal(100000),
            counterparty=Counterparty(
                name="Patricia", paternal_surname="Robles", maternal_surname="Peña"
            ),
            bank_account=MexicanBankAccount(
                clabe="012345678912345678", institution="154"
            ),
        ),
    ),
    deposits=(
        FundsMovement(
            at=datetime(2027, 3, 2, 15, 5, 21, tzinfo=CDMX),
            instrument="4",
            currency="1",
            amount=Decimal(450000),
            counterparty=Counterparty(name="Compañía Exportadora, S.A. de C.V."),
            bank_account=ForeignBankAccount(
                number="987654321987654321", bank="Banca Extranjera"
            ),
        ),
    ),
)

AVISO_FULL = Aviso(
    reference="REF202703001",
    priority=Priority.NORMAL,
    alert_type="4104",
    client=COMPANY_CLIENT,
    beneficial_owners=(OWNER,),
    operations=FULL_OPERATIONS,
)

PERSON_CLIENT = Client(
    account=PlatformAccount(
        user_id="USR-77", related_account="5555000011112222", currency="1"
    ),
    person=NaturalPerson(
        first_names="Ana",
        paternal_surname="Rodríguez",
        maternal_surname="Núñez",
        birth_date=date(1978, 7, 2),
        nationality="MX",
        economic_activity="8121001",
        document=IdDocument(kind="2", number="IDMEX1234567"),
    ),
)


def small_aviso(index: int) -> Aviso:
    """Build an aviso with one purchase, for size-split tests.

    Returns:
        The aviso numbered ``index``.

    """
    return Aviso(
        reference=f"REF{index:06d}",
        alert_type="100",
        client=PERSON_CLIENT,
        operations=Operations(
            purchases=(
                Trade(
                    at=datetime(2027, 3, 1, 12, tzinfo=CDMX) + timedelta(hours=index),
                    currency="1",
                    amount=Decimal(1000 + index),
                    asset=_btc("1900000", "0.0005"),
                    tx_hash=f"{index:064X}",
                ),
            )
        ),
    )


MARCH_2027 = AvisoInforme(month=date(2027, 3, 1), subject=FILER, avisos=(AVISO_FULL,))
