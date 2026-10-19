"""Which URLs the platform will call for a webhook, and how it reaches them.

An endpoint URL is typed in by a tenant and called from inside the platform's own
network, so it is checked when it is saved and again before every delivery. Outside
production only the URL's shape is checked; in production it has to be https and every
address it resolves to has to be public. The request then goes to the address that was
checked, not to a second lookup of the name.
"""

import ipaddress
import socket

import pytest

from tests.platform.webhooks_support import HOOK, PUBLIC_IP, Resolves
from xolotl.platform.config import Settings
from xolotl.platform.events.url_policy import (
    MAX_URL_LENGTH,
    Destination,
    Resolver,
    UrlPolicy,
    WebhookUrlError,
    is_public_address,
    system_resolver,
)

PUBLIC_V4 = PUBLIC_IP
PUBLIC_V6 = "2606:4700:4700::1111"


def strict(resolver: Resolver) -> UrlPolicy:
    return UrlPolicy(strict=True, resolver=resolver)


def hooks_resolving_to(*addresses: str) -> UrlPolicy:
    return strict(Resolves({"hooks.example.com": addresses}))


# Everything below is an address the platform must never call: private, loopback,
# link-local, multicast, reserved, cloud metadata and the IPv6 spellings that carry one
# of those inside them.
NON_PUBLIC = [
    str(ipaddress.IPv4Address(0)),  # 0.0.0.0, which lint reads as a bind address
    "127.0.0.1",
    "127.0.0.53",
    "10.0.0.1",
    "172.16.5.4",
    "172.31.255.255",
    "192.168.0.1",
    "169.254.169.254",
    "169.254.170.2",
    "100.64.0.1",
    "100.100.100.200",
    "192.0.0.192",
    "198.18.0.5",
    "224.0.0.1",
    "239.255.255.250",
    "240.0.0.1",
    "255.255.255.255",
    "168.63.129.16",
    "::",
    "::1",
    "fe80::1",
    "fe80::1%eth0",
    "fc00::1",
    "fd00:ec2::254",
    "ff02::1",
    "ff0e::1",
    "2001:db8::1",
    "2002:7f00:1::1",
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    "::ffff:169.254.169.254",
    "::ffff:168.63.129.16",
    "::7f00:1",  # IPv4-compatible, reserved space that Python counts as global
    "64:ff9b::7f00:1",
    "64:ff9b::a9fe:a9fe",
    "64:ff9b::a83f:8110",
]
PUBLIC = [
    "8.8.8.8",
    "1.1.1.1",
    PUBLIC_V4,
    PUBLIC_V6,
    "::ffff:8.8.8.8",
    "64:ff9b::808:808",
]


def literal(address: str) -> str:
    host = f"[{address}]" if ":" in address else address
    return f"https://{host}/xolotl"


@pytest.mark.parametrize("address", NON_PUBLIC)
def test_non_public_addresses_are_not_public(address):
    assert not is_public_address(address)


@pytest.mark.parametrize("address", PUBLIC)
def test_public_addresses_are_public(address):
    assert is_public_address(address)


def test_text_that_is_not_an_address_is_not_public():
    assert not is_public_address("not-an-address")
    assert not is_public_address("")


# ---- the address a name resolves to ----------------------------------------------


def test_a_public_hostname_is_pinned_to_the_address_that_was_checked():
    resolver = Resolves({"hooks.example.com": [PUBLIC_V4]})
    destination = strict(resolver).destination(f"{HOOK}?x=1")
    assert destination == Destination(
        url=f"https://{PUBLIC_V4}/xolotl?x=1",
        host="hooks.example.com",
        sni_hostname="hooks.example.com",
    )
    assert resolver.calls == ["hooks.example.com"]


def test_a_url_with_whitespace_around_it_is_read_without_it():
    destination = hooks_resolving_to(PUBLIC_V4).destination(
        "  https://hooks.example.com/xolotl\n"
    )
    assert destination.url == f"https://{PUBLIC_V4}/xolotl"


def test_every_destination_resolves_the_name_again():
    resolver = Resolves({"hooks.example.com": [PUBLIC_V4]})
    policy = strict(resolver)
    _ = policy.destination(HOOK)
    _ = policy.destination(HOOK)
    assert resolver.calls == ["hooks.example.com"] * 2


def test_the_host_header_keeps_a_port_that_is_not_the_default():
    destination = hooks_resolving_to(PUBLIC_V4).destination(
        "https://hooks.example.com:8443/xolotl"
    )
    assert destination == Destination(
        url=f"https://{PUBLIC_V4}:8443/xolotl",
        host="hooks.example.com:8443",
        sni_hostname="hooks.example.com",
    )


def test_the_default_port_is_left_out_of_the_host_header():
    destination = hooks_resolving_to(PUBLIC_V4).destination(
        "https://hooks.example.com:443/xolotl"
    )
    assert destination.host == "hooks.example.com"
    assert destination.url == f"https://{PUBLIC_V4}/xolotl"


def test_an_ipv6_address_is_written_in_brackets():
    destination = hooks_resolving_to(PUBLIC_V6).destination(HOOK)
    assert destination.url == f"https://[{PUBLIC_V6}]/xolotl"
    assert destination.host == "hooks.example.com"


def test_an_ipv4_address_is_preferred_to_an_ipv6_one():
    # A host without an IPv6 route fails to connect to the first address it is given.
    destination = hooks_resolving_to(PUBLIC_V6, PUBLIC_V4).destination(HOOK)
    assert destination.url == f"https://{PUBLIC_V4}/xolotl"
    assert hooks_resolving_to(PUBLIC_V6, PUBLIC_V4, "8.8.8.8").destination(
        HOOK
    ).url == (f"https://{PUBLIC_V4}/xolotl")


def test_an_international_name_is_resolved_and_presented_as_punycode():
    resolver = Resolves({"xn--hoks-qqa.example.com": [PUBLIC_V4]})
    destination = strict(resolver).destination("https://hóoks.example.com/xolotl")
    assert resolver.calls == ["xn--hoks-qqa.example.com"]
    assert destination.host == "xn--hoks-qqa.example.com"
    assert destination.sni_hostname == "xn--hoks-qqa.example.com"


def test_the_name_is_compared_in_lower_case():
    resolver = Resolves({"hooks.example.com": [PUBLIC_V4]})
    _ = strict(resolver).destination("https://HOOKS.Example.COM/xolotl")
    assert resolver.calls == ["hooks.example.com"]


@pytest.mark.parametrize("address", NON_PUBLIC)
def test_a_name_that_resolves_to_a_non_public_address_is_refused(address):
    with pytest.raises(WebhookUrlError, match="non-public address"):
        _ = hooks_resolving_to(address).destination(HOOK)


@pytest.mark.parametrize("address", NON_PUBLIC)
def test_a_name_with_one_non_public_address_among_public_ones_is_refused(address):
    # An answer that mixes the two is how a rebinding attack looks from here.
    with pytest.raises(WebhookUrlError, match="non-public address"):
        _ = hooks_resolving_to(PUBLIC_V4, address).destination(HOOK)
    with pytest.raises(WebhookUrlError, match="non-public address"):
        _ = hooks_resolving_to(address, PUBLIC_V4).destination(HOOK)


def test_a_name_that_does_not_resolve_is_refused():
    with pytest.raises(WebhookUrlError, match="does not resolve"):
        _ = strict(Resolves()).destination(HOOK)


def test_a_name_that_resolves_to_nothing_is_refused():
    with pytest.raises(WebhookUrlError, match="does not resolve"):
        _ = hooks_resolving_to().destination(HOOK)


def test_an_answer_that_is_not_an_address_is_refused():
    with pytest.raises(WebhookUrlError, match="does not resolve"):
        _ = hooks_resolving_to("not-an-address").destination(HOOK)


# ---- addresses written in the URL ------------------------------------------------


@pytest.mark.parametrize("address", [PUBLIC_V4, PUBLIC_V6, "8.8.8.8"])
def test_a_public_address_in_the_url_is_called_as_it_is(address):
    resolver = Resolves()
    destination = strict(resolver).destination(literal(address))
    assert destination == Destination(url=literal(address))
    assert resolver.calls == []


@pytest.mark.parametrize("address", [a for a in NON_PUBLIC if "%" not in a])
def test_a_non_public_address_in_the_url_is_refused(address):
    resolver = Resolves()
    with pytest.raises(WebhookUrlError, match="non-public address"):
        _ = strict(resolver).destination(literal(address))
    assert resolver.calls == []


@pytest.mark.parametrize(
    "url",
    [
        "https://2130706433/xolotl",  # 127.0.0.1 as one number
        "https://0x7f.0.0.1/xolotl",  # and with a hexadecimal part
        "https://127.1/xolotl",  # and with the zeros left out
        "https://localhost/xolotl",
        "https://localhost./xolotl",
        "https://LocalHost:8443/xolotl",
    ],
)
def test_the_system_resolver_sees_through_every_spelling_of_loopback(url):
    # The resolver that is used in production asks the operating system, which reads
    # these the way the HTTP client's own lookup would.
    with pytest.raises(WebhookUrlError, match=r"non-public address|not valid"):
        _ = UrlPolicy(strict=True).destination(url)


def test_the_system_resolver_asks_for_stream_addresses_and_lists_each_once(
    monkeypatch,
):
    stream = socket.SOCK_STREAM

    def getaddrinfo(host, port, **options):
        assert (host, port, options) == ("hooks.example.com", None, {"type": stream})
        return [
            (socket.AF_INET6, stream, 6, "", (PUBLIC_V6, 0, 0, 0)),
            (socket.AF_INET, stream, 6, "", (PUBLIC_V4, 0)),
            (socket.AF_INET6, stream, 6, "", (PUBLIC_V6, 0, 0, 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    assert system_resolver("hooks.example.com") == [PUBLIC_V6, PUBLIC_V4]


def test_the_system_resolver_knows_localhost():
    addresses = system_resolver("localhost")
    assert addresses
    assert not any(is_public_address(address) for address in addresses)


def test_the_system_resolver_raises_for_a_name_that_does_not_exist():
    with pytest.raises(socket.gaierror):
        _ = system_resolver("nowhere.invalid")


# ---- the shape of the URL --------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://hooks.example.com/xolotl",
        "HTTP://hooks.example.com/xolotl",
        "ftp://hooks.example.com/xolotl",
        "ws://hooks.example.com/xolotl",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "hooks.example.com/xolotl",
        "//hooks.example.com/xolotl",
        "",
        "   ",
    ],
)
def test_production_calls_https_and_nothing_else(url):
    with pytest.raises(WebhookUrlError, match="https"):
        _ = hooks_resolving_to(PUBLIC_V4).destination(url)


@pytest.mark.parametrize(
    "url",
    [
        "https://user:pw@hooks.example.com/xolotl",
        "https://user@hooks.example.com/xolotl",
        "https://good.example.com\\@hooks.example.com/xolotl",
        "https:///xolotl",
        "https://hooks.example.com:0/xolotl",
        "https://hooks.example.com:65536/xolotl",
        "https://hooks.example.com:99999/xolotl",
        "https://hooks .example.com/xolotl",
        "https://hooks%2eexample.com/xolotl",
        "https://hooks.example.com\t/xolotl",
        "https://hooks.example.com/\x00",
        "https://[::1/xolotl",
        "https://" + "a" * 64 + ".example.com/xolotl",
        "https://" + "a." * 130 + "com/xolotl",
        "https://hooks.example.com/" + "a" * MAX_URL_LENGTH,
    ],
)
@pytest.mark.parametrize("production", [True, False])
def test_a_url_that_is_not_well_formed_is_refused_everywhere(url, production):
    policy = UrlPolicy(
        strict=production, resolver=Resolves({"hooks.example.com": [PUBLIC_V4]})
    )
    with pytest.raises(WebhookUrlError, match=r"not valid|credentials|too long"):
        _ = policy.destination(url)


def test_a_refusal_never_repeats_the_url():
    # URLs often carry a token in the path, and an error ends up in a response, a log
    # line and a column.
    secret_path = "/hooks/tok_8f3a91c2"
    attempts = [
        f"http://hooks.example.com{secret_path}",
        f"https://user:pw@hooks.example.com{secret_path}",
        f"https://hooks.example.com:99999{secret_path}",
        f"https://127.0.0.1{secret_path}",
        f"https://[::1]{secret_path}",
        f"https://nowhere.example.com{secret_path}",
    ]
    for url in attempts:
        with pytest.raises(WebhookUrlError) as caught:
            _ = hooks_resolving_to(PUBLIC_V4).destination(url)
        text = f"{caught.value} {caught.value.detail} {caught.value!r}"
        assert "tok_8f3a91c2" not in text
        assert "example.com" not in text
        assert "127.0.0.1" not in text


def test_a_refusal_is_a_validation_failure():
    with pytest.raises(WebhookUrlError) as caught:
        _ = hooks_resolving_to(PUBLIC_V4).destination("http://hooks.example.com/x")
    assert (caught.value.status, caught.value.code) == (422, "VALIDATION_FAILED")


# ---- outside production ----------------------------------------------------------


def test_outside_production_a_url_is_called_as_written_and_never_resolved():
    resolver = Resolves()
    policy = UrlPolicy(strict=False, resolver=resolver)
    for url in [
        "http://localhost:9000/hook",
        "https://localhost/hook",
        "http://127.0.0.1:8080/hook",
        "http://10.1.2.3/hook",
        "https://hooks.example.com/hook",
        "http://[::1]:9000/hook",
    ]:
        assert policy.destination(url) == Destination(url=url)
    assert resolver.calls == []


@pytest.mark.parametrize(
    "url",
    ["ftp://localhost/hook", "localhost:9000/hook", "file:///etc/passwd", ""],
)
def test_outside_production_only_http_and_https_are_called(url):
    with pytest.raises(WebhookUrlError, match="http"):
        _ = UrlPolicy(strict=False).destination(url)


def test_a_url_longer_than_the_limit_is_refused_even_when_it_would_otherwise_pass():
    url = "https://hooks.example.com/" + "a" * (MAX_URL_LENGTH - 26)
    assert len(url) == MAX_URL_LENGTH
    assert UrlPolicy(strict=False).destination(url) == Destination(url=url)
    with pytest.raises(WebhookUrlError, match="too long"):
        _ = UrlPolicy(strict=False).destination(url + "a")


# ---- the policy a configuration asks for -----------------------------------------


@pytest.mark.parametrize(
    ("env", "expected"), [("prod", True), ("dev", False), ("test", False)]
)
def test_only_production_is_strict(env, expected):
    settings = Settings.model_construct(env=env)
    assert UrlPolicy.for_settings(settings).strict is expected


def test_the_policy_for_a_configuration_resolves_with_the_resolver_it_is_given():
    resolver = Resolves({"hooks.example.com": [PUBLIC_V4]})
    settings = Settings.model_construct(env="prod")
    policy = UrlPolicy.for_settings(settings, resolver=resolver)
    assert policy.destination(HOOK).url == f"https://{PUBLIC_V4}/xolotl"
