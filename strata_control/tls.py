"""How Strata's own programs check the certificate of a server they download from (#3722).

Python's OpenSSL checks a server against a list of root certificates it carries (certifi) and does not fetch a missing intermediate
certificate. Browsers and curl use the Linux system certificate store, which may include a private CA or a proxy certificate.
So a machine where the browser works could still fail with CERTIFICATE_VERIFY_FAILED in Strata.

`install()` makes every later HTTPS connection of this process use the operating system's store, through the `truststore` package
(pure Python, Python 3.10 and newer, pinned in requirements.txt and the runtime locks). Where it is not installed (an older Python, a
checkout that has not installed the pins yet) the certifi list is used; where that is missing too, Python's own default stays.
Verification is never turned off: a connection that still fails is explained by `explain()`, and nothing here offers to skip the check.
"""

from __future__ import annotations

import ssl
import sys

#: "truststore", "certifi" or "default": what the process uses after install()
mode: str | None = None


def install() -> str:
    """Use the OS certificate store for every HTTPS connection of this process. Safe to call again and from any thread's start."""
    global mode
    if mode is not None:
        return mode
    try:
        import truststore                                  # Python >= 3.10
        truststore.inject_into_ssl()
        mode = "truststore"
        return mode
    except Exception:                                      # ImportError, or a Python/OS truststore cannot work on
        pass
    try:
        import certifi
        bundle = certifi.where()
        ssl._create_default_https_context = lambda *a, **k: ssl.create_default_context(cafile=bundle)   # type: ignore[assignment]
        mode = "certifi"
    except Exception:
        mode = "default"
    return mode


def is_certificate_error(exc: BaseException | None) -> bool:
    """True for a failed certificate check, however urllib, http.client or requests wrapped it."""
    seen = 0
    while exc is not None and seen < 6:
        if isinstance(exc, ssl.SSLCertVerificationError) or "CERTIFICATE_VERIFY_FAILED" in str(exc):
            return True
        exc = getattr(exc, "reason", None) if isinstance(getattr(exc, "reason", None), BaseException) else (exc.__cause__ or exc.__context__)
        seen += 1
    return False


TRY = ("Things to try, in this order: 1) check the system clock; 2) switch off HTTPS inspection in a local "
       "security suite for Strata; 3) on a managed network, ask the administrator to trust the download "
       "addresses; 4) refresh the Linux CA bundle. Strata will not skip the check.")


def explain(host: str | None = None) -> str:
    """The plain sentence for a certificate failure: no urlopen text, the likely causes, no offer to turn the check off."""
    where = f"the server {host}" if host else "the download server"
    return (f"Strata could not confirm that it is really talking to {where}: its security certificate was not accepted. "
            "Something between this machine and the internet is probably intercepting secure (HTTPS) connections, often local security software "
            "web scanning or a company or school proxy. " + TRY)


class CertificateProblem(OSError):
    """A download stopped because the server's certificate was not accepted. str() is the plain explanation."""

    def __init__(self, host: str | None = None):
        super().__init__(explain(host))
        self.host = host


def host_of(url: str) -> str | None:
    try:
        return url.split("/")[2]
    except IndexError:
        return None


if __name__ == "__main__":                                 # python -m strata_control.tls: what this process would use
    print(install(), sys.version.split()[0])
