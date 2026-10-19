# SP-01b — Data Protection: Key Provider, Encrypted Fields, Blind Index, Object Store — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every tenant its own data-encryption keys, store sensitive fields as opaque `Encrypted[T]` values that only service code can reveal (with a `VIEW_PII` audit event when a human sees them), support equality lookups through HMAC blind indexes, rotate and destroy tenant keys, and add the object store port with local-filesystem and S3 adapters.

**Architecture:** A `KeyProvider` port (local adapter now, KMS later) manages per-tenant data-encryption keys (DEKs) for the purposes `DATA`, `INDEX` and `BIOMETRIC`, wrapped by a key-encryption key (the local `MASTER_KEY`) and stored in the tenant-isolated `tenant_key` table with a version. Values are encrypted with AES-256-GCM; the ciphertext carries the key version and the tenant id plus purpose as authenticated data, so a value can never be decrypted under another tenant's key. The SQLAlchemy type `EncryptedStr` binds on write using the current tenant context and returns `Encrypted[str]` on read; `reveal()` is explicit. `blind_index` derives deterministic HMAC-SHA256 tags from normalised identifiers under the tenant's `INDEX` key. The object store is a port with `LocalFsObjectStore` and `S3ObjectStore`, every key prefixed by tenant id and verified by sha256 on read.

**Tech Stack:** adds `cryptography` (AES-GCM, HKDF not needed) and `boto3` as runtime dependencies, `moto[s3]` and `boto3-stubs[s3]` (type stubs) as dev dependencies, on top of the 1a stack.

**Spec:** `docs/superpowers/specs/2026-10-18-sp01-platform-kernel-design.md` §3 (config keys `MASTER_KEY`, `OBJECT_STORE`, `OBJECT_STORE_ROOT`, `S3_*`), §8.1–§8.2 (tables and interfaces), §8.4 (object store), §10 (`keys rotate`), §12 rows `test_crypto.py` and `test_object_store.py`, §13 exit criteria; program spec D6 (encryption and the decrypt boundary), D7 (readable names live in a separate index, out of scope here), D10 (`VIEW_PII`).

**Deployment gates:** before any production tenant, the stored response bodies of the idempotency store (`idempotency_key.response_body`, which holds one-time secrets such as a new API key or an initial staff password for 24 hours so a lost response can be replayed) are encrypted at rest with the tenant's `DATA` key. **Closed by migration 0005 (Task B8).**

## Global Constraints

- Everything in plan 1a's Global Constraints still binds (toolchain, gates, session contract, RLS conventions, enum convention, problem codes, logging redaction, commit trailers).
- New configuration keys (prefix `XOLOTL_`): `MASTER_KEY` (base64 of 32 bytes; a dev default; `ENV=prod` refuses the default), `OBJECT_STORE` (`local` default, or `s3`), `OBJECT_STORE_ROOT` (default `./data/local/objects`), `S3_BUCKET`, `S3_ENDPOINT_URL`, `S3_REGION` (unset by default; required when `OBJECT_STORE=s3`).
- `tenant_key(tenant_id, id, purpose, version, wrapped_dek, kek_ref, created_at, retired_at, destroyed_at)` with `PRIMARY KEY (tenant_id, id)`, `UNIQUE (tenant_id, purpose, version)`, FK `tenant_id → tenant.id`, forced RLS with the kernel predicate, an isolation factory, and a `SHARED_TABLES` registry untouched.
- Ciphertext layout: `version (2 bytes big-endian) || nonce (12 bytes) || AES-256-GCM ciphertext+tag`; AAD = `tenant_id.bytes + purpose.encode()`; a fresh random nonce per encryption; keys are 32 bytes.
- `LocalKeyProvider`: KEK = `MASTER_KEY`; `wrapped_dek = AES-GCM(KEK, DEK, aad=tenant_id.bytes + purpose.encode() + version.to_bytes(2, "big"))` with its own 12-byte nonce prepended; `kek_ref = "local:v1"`; DEKs cached in memory for 5 minutes keyed by `(tenant_id, purpose, version)`; `destroy` sets `wrapped_dek = NULL`, `destroyed_at = now()` on every version of every purpose and evicts the cache.
- `Encrypted[T]` is opaque: `repr`/`str` return `Encrypted(***)`, it is never JSON-serialisable by accident (Pydantic rejects it), equality is by ciphertext bytes, and the redaction filter masks it if it ever reaches a log record.
- `reveal_for_response(...)` is the only path that returns plaintext to a human: it decrypts and records `VIEW_PII` with `entity_type`, `entity_id` and `field` in `detail`. Internal processing (TOTP verification) uses `Encrypted.reveal()` and records nothing.
- `blind_index(kind, value, *, session, tenant_id)` = `HMAC-SHA256(index_key, f"{kind}:{normalize_identifier(kind, value)}")`; `normalize_identifier` uppercases, strips whitespace and punctuation, and applies per-kind rules (`email` lowercases and keeps `@` and `.`; `curp`, `rfc`, `clabe`, `phone` keep alphanumerics only).
- Object store keys are always stored under `<tenant_id>/<key>`; `get` recomputes and verifies sha256 and raises `ObjectIntegrityError` on mismatch; `ObjectRef(tenant_id, key, sha256, size, content_type)` is frozen.
- Audit actions added: `tenant.keys_created` (on tenant creation), `tenant.key_rotated` (`detail={"purpose", "version"}`), `tenant.keys_destroyed`, `VIEW_PII` (`detail={"entity_type", "entity_id", "field"}`).
- No plaintext secret or decrypted value may appear in logs, tracebacks or API list views; a test scans the log capture of the crypto suite for every plaintext it used.

## Deviations from the sub-spec (decided here)

- `KeyProvider` methods take a `SessionFactory`, not a `Session`: the SQLAlchemy type decorator and `Encrypted.reveal()` run outside any caller session, so the provider opens its own short `tenant_session(TenantContext.system(tenant_id, "key-provider"))` on cache misses. Callers that already hold a session (tenant creation, rotation) pass `session=` to run in their transaction.
- `Encrypted` carries `tenant_id` and `purpose` captured from the current context at load time (`process_result_value` has no session), plus the version parsed from the ciphertext header; `reveal()` needs no arguments.
- The TOTP secret migration (spec: "converts `staff_credential.totp_secret` to the encrypted form") is a two-step migration (`0003_tenant_key` creates the key table; `0004_encrypt_totp` adds `totp_secret_enc`, backfills with the local provider for tenants that have keys, creates keys for tenants that lack them, drops the plaintext column). The backfill runs inside the migration with the owner engine under `TenantContext.platform(actor, tenant_id=...)` per tenant.
- `S3ObjectStore` takes a boto3 client built by the composition root; tests use `moto`'s `mock_aws`.
- **Implemented as** (plan errata): the S3 adapter, its settings and its dependencies were dropped by the single-container decision (see Tasks B1 and B5); this plan's `S3_*` keys, `boto3`, `moto` and `S3ObjectStore` lines are not built, and the `ObjectStore` port is what a later adapter implements.

## Review Focus

1. A value encrypted under tenant A's key and read back under tenant B's context (same column, forged row) must fail to decrypt with a clear `DecryptionError`, never return garbage or A's plaintext. Test in B3.
2. After `rotate(DATA)`, rows written before the rotation must still reveal (old version readable) and new writes must use the new version; after `destroy`, every reveal for that tenant raises `KeyDestroyedError` and `reveal_for_response` records nothing. Test in B3 and B6.
3. A `MASTER_KEY` of the wrong length or not base64 must fail at startup with a clear settings error, not at the first encryption. Test in B1.
4. `blind_index` must be stable across processes and normalisations (`" ana@Acme.MX "` and `"ana@acme.mx"` share a tag) and must differ across tenants for the same value. Test in B3.
5. The object store must refuse keys that escape the tenant prefix (`../`, absolute paths, empty segments) and must detect a corrupted object on `get`. Test in B5.

## File structure

```
src/xolotl/platform/
  config.py                         (+ MASTER_KEY, OBJECT_STORE, OBJECT_STORE_ROOT, S3_*; validators)
  crypto/__init__.py                re-exports
  crypto/errors.py                  DecryptionError, KeyDestroyedError, KeyNotFoundError (XolotlError subclasses)
  crypto/primitives.py              encrypt_value, decrypt_value, parse_header, new_dek, wrap_dek, unwrap_dek, aad_for
  crypto/models.py                  TenantKey ORM
  crypto/provider.py                KeyPurpose, KeyProvider protocol, LocalKeyProvider, get_key_provider/set_key_provider
  crypto/encrypted.py               Encrypted[T], EncryptedStr TypeDecorator, reveal_for_response
  crypto/blind_index.py             normalize_identifier, blind_index
  storage/__init__.py               ObjectRef, ObjectStore protocol, ObjectIntegrityError, InvalidObjectKeyError, validate_key
  storage/local.py                  LocalFsObjectStore
  storage/s3.py                     S3ObjectStore
  audit/__init__.py                 (+ VIEW_PII action constant)
src/xolotl/modules/tenants/service.py   (create_tenant creates keys; TOTP paths use Encrypted; rotate/destroy helpers)
src/xolotl/modules/tenants/models.py    (StaffCredential.totp_secret: EncryptedStr)
src/xolotl/migrations/versions/0003_tenant_key.py, 0004_encrypt_totp.py
src/xolotl/app.py                        (selects the object store and key provider)
src/xolotl/cli.py                        (keys rotate, keys destroy)
tests/platform/test_config.py (+), test_crypto_primitives.py, test_key_provider.py, test_encrypted.py, test_blind_index.py, test_object_store.py
tests/isolation/factories.py (+ tenant_key), tests/modules/tenants/test_service.py (+ keys on create, TOTP still works), tests/test_cli.py (+ keys)
```

---

### Task B1: Dependencies and configuration

**Implemented as** (plan errata; the code below is the plan as written): `DEV_MASTER_KEY` comes from `DEV_MASTER_KEY_BYTES`, the SHA-256 of a fixed label, and the production refusal compares decoded bytes, so a respelling of the dev key (its unused trailing bits) is refused too; the key is decoded with `base64.b64decode(value, validate=True)` and must be exactly 32 bytes; `Settings` sets `hide_input_in_errors=True`, because pydantic echoed raw input in validation errors (the head and tail of a valid master key, and the JWT secret). The `S3_*` settings, `boto3`, `moto[s3]` and `boto3-stubs[s3]` of this task were removed again by Task B5 (one application container, no S3): `object_store` is `Literal["local"]`, and the README configuration table lists `MASTER_KEY`, `OBJECT_STORE` and `OBJECT_STORE_ROOT`.

**Files:**
- Modify: `pyproject.toml` (runtime: `cryptography>=46`, `boto3>=1.40`; dev: `moto[s3]>=5.1`, `boto3-stubs[s3]>=1.40`), `uv.lock` (via `uv add`), `src/xolotl/platform/config.py`, `.env.example`, `README.md` (configuration section)
- Test: `tests/platform/test_config.py` (append)

**Interfaces produced:** `Settings.master_key: SecretStr` (base64 text; property `master_key_bytes -> bytes` returns the decoded 32 bytes), `Settings.object_store: Literal["local", "s3"]`, `Settings.object_store_root: Path`, `Settings.s3_bucket: str | None`, `Settings.s3_endpoint_url: str | None`, `Settings.s3_region: str | None`; `DEV_MASTER_KEY` constant; validators: `master_key` must decode to exactly 32 bytes (else `ValueError("XOLOTL_MASTER_KEY must be base64 of 32 bytes")`), `ENV=prod` refuses `DEV_MASTER_KEY`, `OBJECT_STORE=s3` requires `S3_BUCKET`.

- [ ] **Step 1: Write the failing tests** (append to `tests/platform/test_config.py`; keep the existing helper for scrubbing env vars)

```python
def test_master_key_defaults_and_decodes(monkeypatch):
    monkeypatch.delenv("XOLOTL_MASTER_KEY", raising=False)
    s = make_settings()
    assert len(s.master_key_bytes) == 32
    assert "***" in repr(s)  # SecretStr never prints


@pytest.mark.parametrize("bad", ["", "not-base64!", base64.b64encode(b"short").decode()])
def test_master_key_must_be_32_bytes(monkeypatch, bad):
    monkeypatch.setenv("XOLOTL_MASTER_KEY", bad)
    with pytest.raises(ValidationError, match="32 bytes"):
        make_settings()


def test_prod_refuses_the_dev_master_key(monkeypatch):
    monkeypatch.setenv("XOLOTL_ENV", "prod")
    monkeypatch.setenv("XOLOTL_JWT_SECRET", "x" * 40)
    monkeypatch.delenv("XOLOTL_MASTER_KEY", raising=False)
    with pytest.raises(ValidationError, match="MASTER_KEY"):
        make_settings()


def test_object_store_defaults_and_s3_requires_a_bucket(monkeypatch):
    monkeypatch.delenv("XOLOTL_OBJECT_STORE", raising=False)
    s = make_settings()
    assert s.object_store == "local"
    assert s.object_store_root == Path("./data/local/objects")
    monkeypatch.setenv("XOLOTL_OBJECT_STORE", "s3")
    with pytest.raises(ValidationError, match="S3_BUCKET"):
        make_settings()
    monkeypatch.setenv("XOLOTL_S3_BUCKET", "xolotl-objects")
    assert make_settings().s3_bucket == "xolotl-objects"
```

(`make_settings` is the file's existing typed seam for `Settings(_env_file=None)`; if the file still uses `cast("Any", Settings)(_env_file=None)` inline, introduce `make_settings` once at the top and reuse it — that is the Task 1 deferred minor.)

- [ ] **Step 2: Run to verify failure.** `uv run pytest tests/platform/test_config.py -v --no-cov` → FAIL (`master_key_bytes` missing).
- [ ] **Step 3: Implement.** `uv add cryptography boto3 && uv add --dev "moto[s3]" "boto3-stubs[s3]"`. In `config.py`: `DEV_MASTER_KEY = base64.b64encode(b"\x00" * 32).decode()` is NOT acceptable (all-zero key); use a fixed random-looking dev constant such as `base64.b64encode(hashlib.sha256(b"xolotl-dev-master-key").digest()).decode()` computed at import; `master_key: SecretStr = Field(default=SecretStr(DEV_MASTER_KEY))`; a `field_validator("master_key")` decoding with `base64.b64decode(value, validate=True)` and checking the length; `master_key_bytes` property; the prod refusal joins the existing `_refuse_dev_secrets_in_prod` validator; the object-store fields and a `model_validator` for the S3 requirement. Update `.env.example` and the README configuration table.
- [ ] **Step 4: Run to verify pass**, then `make lint types importlint`.
- [ ] **Step 5: Commit** `build(platform): crypto and object-store dependencies and settings`.

---

### Task B2: Tenant keys: model, migration 0002, primitives and the local key provider

**Implemented as** (plan errata; the code below is the plan as written): the migration is `0003_tenant_key` (the heading's "0002" is plan 1c's `0002_events_jobs_workflows`, which landed first); built as three commits (primitives, table and migration, provider). `KeyPurpose` lives in `platform/enums.py`; keys are exactly 32 bytes and versions run 0 to 65535, the two bytes of the header, `rotate` refusing one beyond. Every key travels as `SecretBytes` (it prints as a mask) and `wrapped_dek` is a `SecretBinary` column (still `bytea`), so no repr, SQL row log or locals-printing traceback shows a key. The cache is a `KeyCache` with a pointer to each purpose's current version, an epoch that drops a read racing a rotation or destruction, and eviction after the outermost commit (one `after_commit` listener drains the tenants that `rotate` and `destroy` queue on `Session.info`); its clock is `time.monotonic` (`clock_fn: Callable[[], float]`, not a `datetime` function), and a read through the caller's `session=` never touches it. `rotate` locks the purpose's rows before it reads (INDEX is refused first; a tenant with no keys is `KeyNotFoundError`, a destroyed one `KeyDestroyedError`). Destruction is final: `create_tenant_keys` locks, reads and raises `KeyDestroyedError` for a tenant with any destroyed key, while the provider's `destroy` of a tenant with no keys does nothing. The process-wide provider sits in a small `_ProviderSlot` rather than a module global. `kek_ref` is not the constant `local:v1`: Task B7 made it `local:v1:<16 hex>`, a fingerprint of the master key, checked on reads and before writes.

**Files:**
- Create: `src/xolotl/platform/crypto/__init__.py`, `errors.py`, `primitives.py`, `models.py`, `provider.py`
- Create: `src/xolotl/migrations/versions/0003_tenant_key.py` (revision `0003_tenant_key`, `down_revision = "0002_events_jobs_workflows"` — plan 1c's Task E1 migration, which lands first)
- Modify: `src/xolotl/models.py` (import `crypto.models`), `src/xolotl/platform/enums.py` (`KeyPurpose`), `tests/isolation/factories.py` (factory for `tenant_key`), `tests/helpers.py` (`make_tenant_with_keys`)
- Test: `tests/platform/test_crypto_primitives.py`, `tests/platform/test_key_provider.py`

**Interfaces produced:**

```python
class KeyPurpose(StrEnum): DATA = "DATA"; INDEX = "INDEX"; BIOMETRIC = "BIOMETRIC"

# errors.py — all subclass XolotlError
class DecryptionError(XolotlError): status = 500; default_code = "DECRYPTION_FAILED"
class KeyDestroyedError(XolotlError): status = 410; default_code = "KEY_DESTROYED"
class KeyNotFoundError(XolotlError): status = 500; default_code = "KEY_NOT_FOUND"

# primitives.py
HEADER_LEN = 2; NONCE_LEN = 12; KEY_LEN = 32
def aad_for(tenant_id: UUID, purpose: KeyPurpose) -> bytes            # tenant_id.bytes + purpose.value.encode()
def new_dek() -> bytes                                                 # secrets.token_bytes(32)
def encrypt_value(dek: bytes, *, version: int, plaintext: bytes, aad: bytes) -> bytes
def decrypt_value(dek: bytes, *, ciphertext: bytes, aad: bytes) -> bytes   # raises DecryptionError
def parse_header(ciphertext: bytes) -> int                             # version; raises DecryptionError on short input
def wrap_dek(kek: bytes, dek: bytes, *, tenant_id: UUID, purpose: KeyPurpose, version: int) -> bytes
def unwrap_dek(kek: bytes, wrapped: bytes, *, tenant_id: UUID, purpose: KeyPurpose, version: int) -> bytes

# models.py
class TenantKey(TenantTableMixin, Base):  __tablename__ = "tenant_key"
    purpose: Mapped[KeyPurpose]; version: Mapped[int]; wrapped_dek: Mapped[bytes | None]; kek_ref: Mapped[str]
    created_at: Mapped[datetime]; retired_at: Mapped[datetime | None]; destroyed_at: Mapped[datetime | None]

# provider.py
class KeyProvider(Protocol):
    def create_tenant_keys(self, tenant_id: UUID, *, session: Session | None = None) -> None: ...
    def data_key(self, tenant_id: UUID, purpose: KeyPurpose, version: int | None = None, *, session: Session | None = None) -> tuple[int, bytes]: ...
    def index_key(self, tenant_id: UUID, *, session: Session | None = None) -> bytes: ...
    def rotate(self, tenant_id: UUID, purpose: KeyPurpose, *, session: Session | None = None) -> int: ...
    def destroy(self, tenant_id: UUID, *, session: Session | None = None) -> None: ...
class LocalKeyProvider(KeyProvider):
    def __init__(self, kek: bytes, session_factory: SessionFactory, *, cache_ttl: timedelta = timedelta(minutes=5), clock_fn: Callable[[], datetime] = clock.now) -> None
    KEK_REF = "local:v1"
def get_key_provider() -> KeyProvider      # raises RuntimeError("key provider not configured") until set
def set_key_provider(provider: KeyProvider | None) -> None
```

Semantics: `create_tenant_keys` inserts version 1 for each of the three purposes (idempotent: existing purposes are left alone); `data_key(version=None)` returns the newest non-retired, non-destroyed version `(version, dek)`; an explicit version returns that version even if retired (old rows must stay readable) but raises `KeyDestroyedError` if `wrapped_dek IS NULL`; `index_key` is `data_key(tenant_id, INDEX)[1]` (never rotated in 1b; rotation of INDEX keys needs re-indexing and is refused with `DomainError`); `rotate` inserts `version = max + 1` and sets `retired_at` on the previous current version; `destroy` nulls every `wrapped_dek`, sets `destroyed_at`, evicts the cache and records nothing itself (the caller audits). When `session` is None the provider opens `tenant_session(TenantContext.system(tenant_id, "key-provider"), factory=self._session_factory)` for the operation. The cache is a dict guarded by a `threading.Lock` with `(dek, expires_at)` values.

Migration `0003_tenant_key` creates the table with `sa.String(32)` for `purpose`, the PK `(tenant_id, id)`, `UNIQUE (tenant_id, purpose, version)`, the FK to `tenant`, and `enable_rls("tenant_key")`.

- [ ] **Step 1: Write the failing tests.** `test_crypto_primitives.py`: round trip (`decrypt_value(encrypt_value(...))`), different nonce per call, header carries the version, wrong AAD raises `DecryptionError`, tampered byte raises, truncated input raises, `wrap_dek`/`unwrap_dek` round trip and refuse a different tenant/purpose/version. `test_key_provider.py` (DB): `create_tenant_keys` creates three version-1 rows visible only under that tenant (RLS) and is idempotent; `data_key` returns `(1, 32 bytes)` and caches (patch `clock_fn` to expire the cache and assert a second DB read); `rotate(DATA)` returns 2, retires 1, `data_key()` returns version 2 while `data_key(version=1)` still works; `rotate(INDEX)` raises `DomainError`; `destroy` makes every `data_key` raise `KeyDestroyedError` and `wrapped_dek` is NULL in the table; two tenants never share a DEK; `get_key_provider()` raises before `set_key_provider`. Isolation factory for `tenant_key` added (the behavioural harness fails otherwise).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** the five modules and the migration exactly per the interfaces; `cryptography.hazmat.primitives.ciphers.aead.AESGCM` for both value encryption and DEK wrapping; `schema-sync` test must stay green (model matches migration).
- [ ] **Step 4: Run to verify pass** (`tests/platform/test_crypto_primitives.py`, `test_key_provider.py`, `tests/isolation`, `tests/platform/test_schema_sync.py`), then `make lint types importlint`.
- [ ] **Step 5: Commit** `feat(crypto): tenant keys, AES-GCM primitives and the local key provider`.

---

### Task B3: `Encrypted[T]`, the `EncryptedStr` column type, `reveal_for_response` and blind indexes

**Implemented as** (plan errata; the code below is the plan as written): `Encrypted` is a frozen, slotted dataclass whose `repr` and `str` mask it and whose class sets `log_opaque = True`, which `logging.redact` recognises by attribute, so `logging.py` imports nothing from the crypto package. `reveal(*, session=None)`, `encrypt(..., session=None)` and `blind_index(..., session=None)` accept the caller's session. Plain text assigned to an attribute is sealed in a `before_flush` hook under the tenant of its own row (`TenantScopeError` for a row with none yet, or of another tenant than a tenant-bound session's); `process_bind_param` seals only text bound outside a flush and refuses an `Encrypted` that is not DATA; an ORM bulk UPDATE with text expires the attributes it left as text. `reveal_for_response` records `VIEW_PII` with the entity in the event's `entity_type` and `entity_id` and `detail={"field"}` (the constraints put all three in `detail`), refuses a value of another tenant before it records anything, and rolls the event back when the decryption fails; `VIEW_PII` is defined in `audit/chain.py` and re-exported. Normalisation: `email` removes whitespace and keeps punctuation, `phone` keeps digits, `name` keeps accents, `rfc` keeps `Ñ` and `&`, and a value with nothing left raises `ValueError`. `master_key` joined the redacted keys, the `sqlalchemy.engine` logger is held at INFO or quieter (a log filter drops its DEBUG rows as well), and a 410 problem is titled "Gone".

**Files:**
- Create: `src/xolotl/platform/crypto/encrypted.py`, `src/xolotl/platform/crypto/blind_index.py`
- Modify: `src/xolotl/platform/logging.py` (redact `Encrypted` instances to `***`), `src/xolotl/platform/audit/__init__.py` (export `VIEW_PII = "VIEW_PII"`)
- Test: `tests/platform/test_encrypted.py`, `tests/platform/test_blind_index.py`

**Interfaces produced:**

```python
# encrypted.py
@dataclass(frozen=True, slots=True)
class Encrypted[T]:
    ciphertext: bytes
    tenant_id: UUID
    purpose: KeyPurpose
    version: int                     # parsed from the header
    _codec: Codec[T]                 # bytes <-> T; StrCodec for str
    def reveal(self) -> T            # provider.data_key(tenant_id, purpose, version) -> decrypt; raises DecryptionError / KeyDestroyedError
    def __repr__(self) -> str: return "Encrypted(***)"
    __str__ = __repr__

def encrypt(value: str, *, tenant_id: UUID, purpose: KeyPurpose = KeyPurpose.DATA) -> Encrypted[str]   # uses the provider's current version

class EncryptedStr(TypeDecorator[Encrypted[str]]):
    impl = LargeBinary; cache_ok = True
    # bind: accepts Encrypted[str] (stored as-is) or str (encrypted now under require_context().require_tenant_id() with purpose DATA)
    # result: Encrypted[str] bound to the current context's tenant (require_context()), purpose DATA; None stays None

def reveal_for_response(session: Session, value: Encrypted[str], *, entity_type: str, entity_id: str, field: str) -> str
    # record(session, AuditEvent(action=VIEW_PII, entity_type=..., entity_id=..., detail={"field": field})) then value.reveal()

# blind_index.py
def normalize_identifier(kind: str, value: str) -> str
def blind_index(kind: str, value: str, *, tenant_id: UUID) -> bytes     # HMAC-SHA256(index_key, f"{kind}:{normalized}") via get_key_provider().index_key
```

Normalisation rules: strip, NFKC; `email` → lowercase, remove spaces; `phone` → digits only (keep a leading `+` as `00`? no: digits only, country code as given); `curp`, `rfc`, `clabe`, `name`, default → uppercase, keep `[A-Z0-9]` only (names also keep spaces collapsed to one); unknown kinds use the default. Document the table in the module docstring.

Redaction: `redact()` in `logging.py` returns `"***"` for any `Encrypted` instance (import under `TYPE_CHECKING` plus a duck-typed check on `__class__.__name__ == "Encrypted"` to avoid a platform-internal import cycle, or import `Encrypted` directly if no cycle exists — say which).

- [ ] **Step 1: Write the failing tests.** `test_encrypted.py` (DB, uses `make_tenant_with_keys`): round trip through a real `EncryptedStr` column (use `StaffCredential.totp_secret` after B4, or a throwaway mapped table declared in the test on `Base` with its own `tenant_table_args` — prefer the throwaway table created via `Base.metadata.create_all(tables=[...])` on the admin engine with RLS statements applied, then dropped; reads under tenant A reveal the plaintext; the same row read under tenant B's context (insert as B with A's ciphertext forged through the admin engine) raises `DecryptionError`; after `rotate(DATA)` old rows reveal and new writes carry version 2 in the header; after `destroy` reveal raises `KeyDestroyedError`; `repr`/`str` are `Encrypted(***)`; `json.dumps` of a dict containing it fails and `redact` masks it; `reveal_for_response` returns the plaintext and writes exactly one `VIEW_PII` audit row with the field detail; a log capture of the whole module shows none of the plaintexts used (collect them in a list and scan `caplog.text` at the end). `test_blind_index.py`: equal tags for `" ana@Acme.MX "` and `"ana@acme.mx"`; different kinds give different tags for the same value; different tenants give different tags; the tag is 32 bytes and stable across two provider instances with the same KEK and database; `normalize_identifier` table cases (`"gómez pérez"` name → `"GOMEZ PEREZ"` after NFKC strips accents? — no: NFKC does not strip accents; keep accents out of scope and document that names are uppercased with spaces collapsed and non-alphanumerics other than space removed, accents preserved; test that rule).
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement** `encrypted.py`, `blind_index.py`, the `redact` change and the `VIEW_PII` constant.
- [ ] **Step 4: Run to verify pass**, `make lint types importlint`.
- [ ] **Step 5: Commit** `feat(crypto): Encrypted values, the EncryptedStr column type, reveal_for_response and blind indexes`.

---

### Task B4: Tenant keys on creation, encrypted TOTP secrets, migration 0004

**Implemented as** (plan errata; the code below is the plan as written): built as five commits. `build_key_provider(settings, session_factory)` in `app.py` is the one place that picks the adapter, for `build_app` and for the CLI's `_ensure_key_provider()`, which installs one provider per process for `tenant create` and `keys`. `login` and `login_totp` run inside `_in_tenant`, which binds the system context `auth` and applies it (a loaded `Encrypted` needs the bound context); TOTP verification uses `reveal(session=s)`, so a destruction committed by another process is honoured at once, and a destroyed key ends the second step as a 410 without counting an attempt. Migration 0004 uses one `tenant_session` per tenant on the owner's connection, skips tenants whose keys were destroyed, logs instead of auditing the keys it creates, refuses to drop the plaintext column while a secret is unsealed, drops it and rewrites the table after (so no live page keeps the plaintext), and clears the `app.*` settings its transaction shares; the downgrade opens each secret with the version in its header. It is not safe for a rolling deploy and needs `MASTER_KEY` in both directions. It reaches `tenant_key` through the live `TenantKey` model (`LocalKeyProvider`), which a migration should not: freeze the table inside 0004 before `tenant_key` next changes. `db upgrade` configures logging.

**Files:**
- Create: `src/xolotl/migrations/versions/0004_encrypt_totp.py` (revision `0004_encrypt_totp`, `down_revision = "0003_tenant_key"`)
- Modify: `src/xolotl/modules/tenants/models.py` (`StaffCredential.totp_secret: Mapped[Encrypted[str] | None] = mapped_column(EncryptedStr)`), `src/xolotl/modules/tenants/service.py` (`create_tenant` calls `get_key_provider().create_tenant_keys(tenant.id, session=s)` and records `tenant.keys_created`; `enroll_totp` stores `encrypt(secret, tenant_id=...)`; `confirm_totp` and `login_totp` call `.reveal()` for verification), `src/xolotl/app.py` (`set_key_provider(LocalKeyProvider(settings.master_key_bytes, session_factory))` in `build_app`), `tests/conftest.py` (configure the provider for the test session after `engines`), `tests/isolation/factories.py` (credential factory writes an `Encrypted` or None)
- Test: `tests/modules/tenants/test_service.py` (append), `tests/platform/test_migration_0004.py`

Migration 0004: `ALTER TABLE staff_credential ADD COLUMN totp_secret_enc bytea`; for every tenant (owner engine, platform context with `tenant_id`): ensure keys exist (`LocalKeyProvider(settings.master_key_bytes, sessionmaker(bind=connection))`), encrypt each non-null `totp_secret` into `totp_secret_enc`; then drop `totp_secret` and rename `totp_secret_enc` to `totp_secret`. Downgrade reverses with decryption. The migration imports the provider and primitives (allowed; kernel helpers are already imported by 0001). The schema-sync test keeps the model and the catalog aligned (the column type stays `bytea`).

- [ ] **Step 1: Write the failing tests.** Service: `create_tenant` leaves three `tenant_key` rows and a `tenant.keys_created` audit event; TOTP enrol/confirm/login still work end to end (existing tests) and the stored `totp_secret` bytes are not the base32 secret (`select(StaffCredential.totp_secret)` as raw bytes via `column(...)`/`text()` under the tenant); the isolation harness still passes for `staff_credential`. `test_migration_0004.py`: on a scratch schema at revision 0003 (use `alembic downgrade 0002` on the test database inside the test, insert a tenant and a credential with a plaintext secret via the admin engine, `upgrade head`, assert the secret reads back as `Encrypted` and reveals to the original, then `downgrade 0003`, assert plaintext restored, `upgrade head` again) — this test is marked to run last in its module and restores `head`.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run to verify pass** (`tests/modules/tenants`, `tests/isolation`, `tests/platform/test_schema_sync.py`, `test_migration_0004.py`), `make lint types importlint`.
- [ ] **Step 5: Commit** `feat(tenants): tenant keys on creation and encrypted TOTP secrets (migration 0004)`.

---

### Task B5: Object store port with local-filesystem and S3 adapters

**Implemented as** (plan errata; the code below is the plan as written): no S3: the product owner decided on one application container, and S3 is a later adapter behind the same port. There is no `s3.py`, `S3ObjectStore`, `boto3`, `moto` or `S3_*` setting; `object_store` is `Literal["local"]` and `build_object_store(settings)` raises on anything else. The port lives in `storage/port.py`, which `storage/__init__.py` re-exports (ruff rejects code in an `__init__`), and `Path.replace` stands for `os.replace`. A missing object is `NotFoundError`. For every adapter the port validates, before anything is written, keys (the `.meta.json` suffix is reserved, lone surrogates are refused), list prefixes, content types and metadata (`InvalidObjectMetadataError`). `LocalFsObjectStore` stages its files in `<root>/.tmp` and moves the bytes in before the record, refuses symlinks under the root and a tenant id that is not a `UUID`, and prunes the directories a delete empties; `AppState.object_store` carries it. The tests run the local adapter only (no `moto`).

**Files:**
- Create: `src/xolotl/platform/storage/__init__.py`, `local.py`, `s3.py`
- Modify: `src/xolotl/platform/api/app.py` (`AppState.object_store`), `src/xolotl/app.py` (select by `OBJECT_STORE`)
- Test: `tests/platform/test_object_store.py`

**Interfaces produced:**

```python
@dataclass(frozen=True, slots=True)
class ObjectRef: tenant_id: UUID; key: str; sha256: bytes; size: int; content_type: str

class ObjectIntegrityError(XolotlError): status = 500; default_code = "OBJECT_CORRUPT"
class InvalidObjectKeyError(ValidationFailedError): default_detail = "invalid object key"

def validate_key(key: str) -> str
    # 1–512 chars; segments split on "/"; no empty segment, no ".", no "..", no backslash, no NUL, no leading "/"; returns the key

class ObjectStore(Protocol):
    def put(self, tenant_id: UUID, key: str, data: bytes, *, content_type: str, metadata: Mapping[str, str] = ...) -> ObjectRef: ...
    def get(self, ref: ObjectRef) -> bytes: ...                      # verifies sha256 -> ObjectIntegrityError
    def delete(self, ref: ObjectRef) -> None: ...                    # idempotent
    def list(self, tenant_id: UUID, prefix: str) -> Iterator[ObjectRef]: ...

class LocalFsObjectStore(ObjectStore):  __init__(self, root: Path)
    # <root>/<tenant_id>/<key> plus <key>.meta.json {sha256 hex, size, content_type, metadata}; atomic write via temp file + os.replace; list walks the tenant dir
class S3ObjectStore(ObjectStore):  __init__(self, bucket: str, client: S3Client)
    # object key "<tenant_id>/<key>"; ContentType and Metadata {"sha256": hex, **metadata}; list via paginator with Prefix
```

- [ ] **Step 1: Write the failing tests** parametrised over both adapters (`tmp_path` for local; `moto.mock_aws` with a `boto3.client("s3", region_name="us-east-1")` and a created bucket for S3): put/get/list/delete round trip; `get` after corrupting the stored bytes raises `ObjectIntegrityError` (local: rewrite the file; S3: `put_object` over the key with other bytes); tenant prefixing (`list` of tenant B does not see tenant A's objects; a ref for tenant A used with B's prefix still points at A's path — refs are explicit); `validate_key` rejects `"../x"`, `"/abs"`, `"a//b"`, `"a/./b"`, `""`, `"x\\y"`, a 513-char key, and accepts `"docs/2026/10/file.pdf"`; `delete` twice is fine; metadata round-trips.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement**; `app.py` builds `LocalFsObjectStore(settings.object_store_root)` or `S3ObjectStore(settings.s3_bucket, boto3.client("s3", endpoint_url=settings.s3_endpoint_url, region_name=settings.s3_region))` and stores it on `AppState`; a unit test asserts the selection from settings.
- [ ] **Step 4: Run to verify pass**, `make lint types importlint` (boto3 typing via `boto3-stubs[s3]`'s `S3Client`).
- [ ] **Step 5: Commit** `feat(storage): object store port with local filesystem and S3 adapters`.

---

### Task B6: Key rotation and destruction: service, audit and CLI

**Implemented as** (plan errata; the code below is the plan as written): built as two commits (service, then CLI). `destroy_tenant_keys` locks the tenant's key rows in id order, as rotation does, before it audits and destroys, which avoids a deadlock with a rotation under way; it refuses a tenant with no keys (`KeyNotFoundError`, nothing audited), and a repeated destroy is audited each time. `--purpose` accepts `INDEX` at the parser and the provider refuses it (exit 1, `CONFLICT`); `keys destroy` without `--yes` exits 2 before any database work; its output says it is irreversible, that other servers keep cached keys for up to five minutes and that backups and WAL still hold the wrapped keys. Refused operations are not audited. Both commands call `_ensure_key_provider()`. The TOTP-level tests of the brief waited for Task B4 and are in Task B7 (`tests/modules/tenants/test_totp_keys.py`).

**Files:**
- Modify: `src/xolotl/modules/tenants/service.py` (`rotate_tenant_key(session, purpose) -> int` and `destroy_tenant_keys(session) -> None`, both auditing `tenant.key_rotated` / `tenant.keys_destroyed`), `src/xolotl/cli.py` (`keys rotate --tenant <slug> --purpose DATA|BIOMETRIC`, `keys destroy --tenant <slug> --yes`)
- Test: `tests/modules/tenants/test_service.py` (append), `tests/test_cli.py` (append, before the reset test)

- [ ] **Step 1: Write the failing tests.** Service: rotate returns 2 and audits with `{"purpose": "DATA", "version": 2}`; a TOTP secret enrolled before rotation still verifies after it (old version readable) and a secret enrolled after rotation carries version 2; `destroy` audits, then `login_totp` for that tenant fails with `KeyDestroyedError` mapped to a 410 problem over HTTP (`GET /v1/auth/me` still works; TOTP verification does not). CLI: `keys rotate --tenant acme --purpose DATA` prints the new version; `--purpose INDEX` exits 1 with the `CONFLICT` message; `keys destroy --tenant acme` without `--yes` exits 2; with `--yes` the keys are nulled.
- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement.** The CLI runs under `TenantContext.platform(actor, tenant_id=...)` like `audit verify`.
- [ ] **Step 4: Run to verify pass**, `make lint types importlint`.
- [ ] **Step 5: Commit** `feat(tenants): key rotation and destruction with audit and CLI`.

---

### Task B7: Full gate, docs and errata

**Implemented as** (plan errata; the code below is the plan as written): besides the docs and errata, this task made `kek_ref` a fingerprint of the master key and checked it on reads and before writes (`provider.py`, `KekMismatchError`, `tests/platform/test_kek_ref.py`, `tests/test_cli_master_key.py`), wrote the TOTP-level rotation and destruction tests over HTTP, rewrote spec section 8 to what was built with section 8.5 listing the departures, and removed the S3 wording from the spec. The deployment gate named in this plan's header, the idempotency store's stored bodies, is closed by migration 0005 (Task B8).

- [ ] `make ci` green on the merged tree; `docker build` still succeeds (new runtime dependencies in the lock).
- [ ] README: configuration table gains the new keys; a "Data protection" section explains tenant keys, `Encrypted` values, `reveal_for_response`/`VIEW_PII`, blind indexes, rotation and destruction, and the object store adapters.
- [ ] Spec errata (sub-spec §8): provider takes a `SessionFactory`; `Encrypted` carries tenant and purpose; the two-step TOTP migration; INDEX keys are not rotatable in 1b.
- [ ] Commit `docs: data protection guide and SP-01b errata`.

---

## Exit criteria for plan 1b (spec §13 rows owned here)

An encrypted field round-trips per tenant and fails across tenants; destroying a tenant's keys makes its encrypted rows unreadable; rotation keeps old rows readable; blind indexes support equality lookups; `VIEW_PII` is recorded whenever a revealed value leaves the service layer; the local and S3 object stores pass the same suite; no plaintext appears in logs during the crypto suite; `make ci` green.
