"""Zero-Signature Cloudflare Wildcard TLS & ACME DNS-01 Management Service."""

from __future__ import annotations

import asyncio
import base64
import datetime
import json
import logging
from typing import Any

import aiohttp
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa, utils
from cryptography.x509.oid import NameOID
from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import get_settings
from database.models import Server
from database.repositories.servers_repo import get_active_servers, get_server_xray_api_url
from database.repositories.system_settings_repo import get_system_setting, set_system_setting
from services.xray_node_client import XrayNodeClient

logger = logging.getLogger(__name__)

_BACKGROUND_TASKS: set[asyncio.Task] = set()


def _b64url(data: bytes) -> str:
    """Base64url-encode bytes without padding (RFC 7515 / RFC 8555)."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def is_certificate_valid(fullchain_pem: str, min_days: int = 30) -> bool:
    """Return True if certificate is valid for at least `min_days` days."""
    if not fullchain_pem or "-----BEGIN CERTIFICATE-----" not in fullchain_pem:
        return False
    try:
        cert = x509.load_pem_x509_certificate(fullchain_pem.encode("utf-8"))
        now = datetime.datetime.now(datetime.timezone.utc)
        # Use timezone-aware not_valid_after_utc if available (cryptography >= 42.0)
        not_after = getattr(cert, "not_valid_after_utc", None)
        if not_after is None:
            not_after = cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
        remaining = not_after - now
        return remaining.total_seconds() > (min_days * 86400)
    except Exception as exc:
        logger.warning("Error parsing certificate: %s", exc)
        return False


class CloudflareDnsClient:
    """Async client for Cloudflare DNS Records API."""

    BASE_URL = "https://api.cloudflare.com/client/v4"

    def __init__(self, api_token: str, session: aiohttp.ClientSession | None = None):
        self.api_token = api_token.strip()
        self._session = session
        self._owns_session = False

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
            self._owns_session = True
        return self._session

    async def close(self) -> None:
        if self._owns_session and self._session and not self._session.closed:
            await self._session.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    async def get_zone_id(self, domain: str) -> str | None:
        """Find Cloudflare Zone ID for the apex domain."""
        parts = domain.strip().lower().lstrip("*.").split(".")
        apex = ".".join(parts[-2:]) if len(parts) >= 2 else domain
        url = f"{self.BASE_URL}/zones?name={apex}&status=active"
        session = await self._get_session()
        try:
            async with session.get(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=10.0)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    results = data.get("result", [])
                    if results:
                        return results[0]["id"]
                logger.warning("Cloudflare zone lookup for %s returned status %d", apex, resp.status)
        except Exception as exc:
            logger.error("Cloudflare zone lookup failed for %s: %s", apex, exc)
        return None

    async def create_txt_record(self, zone_id: str, name: str, content: str, ttl: int = 60) -> str | None:
        """Create a DNS TXT record for ACME challenge."""
        url = f"{self.BASE_URL}/zones/{zone_id}/dns_records"
        payload = {
            "type": "TXT",
            "name": name,
            "content": content,
            "ttl": ttl,
        }
        session = await self._get_session()
        try:
            async with session.post(url, headers=self._headers(), json=payload, timeout=aiohttp.ClientTimeout(total=10.0)) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    record_id = data.get("result", {}).get("id")
                    logger.info("Created Cloudflare TXT record %s for ACME challenge (id: %s)", name, record_id)
                    return record_id
                body = await resp.text()
                logger.error("Failed to create Cloudflare TXT record %s: %s", name, body)
        except Exception as exc:
            logger.error("Exception creating Cloudflare TXT record %s: %s", name, exc)
        return None

    async def delete_dns_record(self, zone_id: str, record_id: str) -> bool:
        """Delete a DNS record from Cloudflare."""
        url = f"{self.BASE_URL}/zones/{zone_id}/dns_records/{record_id}"
        session = await self._get_session()
        try:
            async with session.delete(url, headers=self._headers(), timeout=aiohttp.ClientTimeout(total=10.0)) as resp:
                if resp.status == 200:
                    logger.info("Deleted Cloudflare temporary ACME record %s", record_id)
                    return True
        except Exception as exc:
            logger.warning("Failed to delete Cloudflare record %s: %s", record_id, exc)
        return False


class AcmeDns01Client:
    """Async ACME RFC 8555 Client for Let's Encrypt DNS-01 challenges."""

    def __init__(
        self,
        directory_url: str,
        cf_client: CloudflareDnsClient,
        email: str,
        account_key_pem: str | None = None,
    ):
        self.directory_url = directory_url
        self.cf_client = cf_client
        self.email = email
        self._account_key = self._load_or_create_account_key(account_key_pem)
        self.account_url: str | None = None
        self._directory: dict[str, str] = {}
        self._session: aiohttp.ClientSession | None = None

    def _load_or_create_account_key(self, pem_str: str | None) -> ec.EllipticCurvePrivateKey:
        if pem_str:
            try:
                loaded = serialization.load_pem_private_key(pem_str.encode("utf-8"), password=None)
                if isinstance(loaded, ec.EllipticCurvePrivateKey):
                    return loaded
            except Exception:
                pass
        return ec.generate_private_key(ec.SECP256R1())

    def export_account_key_pem(self) -> str:
        return self._account_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

    def _jwk_thumbprint(self) -> str:
        pn = self._account_key.public_key().public_numbers()
        jwk_dict = {
            "crv": "P-256",
            "kty": "EC",
            "x": _b64url(pn.x.to_bytes(32, "big")),
            "y": _b64url(pn.y.to_bytes(32, "big")),
        }
        canonical_json = json.dumps(jwk_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
        digest = hashes.Hash(hashes.SHA256())
        digest.update(canonical_json)
        return _b64url(digest.finalize())

    async def _get_nonce(self) -> str:
        url = self._directory["newNonce"]
        async with self._session.head(url, timeout=aiohttp.ClientTimeout(total=10.0)) as resp:
            nonce = resp.headers.get("Replay-Nonce")
            if not nonce:
                raise RuntimeError("Failed to obtain ACME Replay-Nonce")
            return nonce

    def _sign_jws(self, payload: dict[str, Any] | str, url: str, nonce: str) -> dict[str, Any]:
        protected: dict[str, Any] = {
            "alg": "ES256",
            "nonce": nonce,
            "url": url,
        }
        if self.account_url:
            protected["kid"] = self.account_url
        else:
            pn = self._account_key.public_key().public_numbers()
            protected["jwk"] = {
                "crv": "P-256",
                "kty": "EC",
                "x": _b64url(pn.x.to_bytes(32, "big")),
                "y": _b64url(pn.y.to_bytes(32, "big")),
            }

        protected_b64 = _b64url(json.dumps(protected, separators=(",", ":")).encode("utf-8"))
        if isinstance(payload, str):
            payload_b64 = _b64url(payload.encode("utf-8"))
        elif payload == "":
            payload_b64 = ""
        else:
            payload_b64 = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))

        signing_input = f"{protected_b64}.{payload_b64}".encode("ascii")
        sig_der = self._account_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = utils.decode_dss_signature(sig_der)
        sig_raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
        sig_b64 = _b64url(sig_raw)

        return {
            "protected": protected_b64,
            "payload": payload_b64,
            "signature": sig_b64,
        }

    async def _post(self, url: str, payload: dict[str, Any] | str) -> tuple[int, dict[str, Any], aiohttp.ClientResponse]:
        headers = {"Content-Type": "application/jose+json"}
        for attempt in range(3):
            nonce = await self._get_nonce()
            jws_body = self._sign_jws(payload, url, nonce)
            async with self._session.post(url, headers=headers, json=jws_body, timeout=aiohttp.ClientTimeout(total=20.0)) as resp:
                status = resp.status
                if status == 400:
                    err_data = await resp.json()
                    if "badNonce" in err_data.get("type", "") and attempt < 2:
                        logger.debug("Bad nonce from ACME server, retrying...")
                        await asyncio.sleep(0.5)
                        continue
                    return status, err_data, resp
                try:
                    data = await resp.json()
                except Exception:
                    data = {}
                return status, data, resp
        raise RuntimeError("Failed ACME request after nonce retries")

    async def issue_wildcard_certificate(self, base_domain: str) -> tuple[str, str]:
        """Issue a Wildcard certificate (*.domain, domain) using DNS-01 via Cloudflare."""
        clean_domain = base_domain.strip().lower().lstrip("*.")
        zone_id = await self.cf_client.get_zone_id(clean_domain)
        if not zone_id:
            raise RuntimeError(f"Could not find Cloudflare Zone ID for {clean_domain}")

        created_records: list[str] = []
        timeout = aiohttp.ClientTimeout(total=30.0)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self._session = session
            # 1. Directory
            async with session.get(self.directory_url) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"ACME directory failed with HTTP {resp.status}")
                self._directory = await resp.json()

            # 2. Account
            new_acc_url = self._directory["newAccount"]
            acc_payload = {
                "termsOfServiceAgreed": True,
                "contact": [f"mailto:{self.email}"],
            }
            status, acc_data, resp = await self._post(new_acc_url, acc_payload)
            if status not in (200, 201):
                raise RuntimeError(f"ACME account registration failed: {acc_data}")
            self.account_url = resp.headers.get("Location")

            # 3. New Order for *.domain and domain
            new_order_url = self._directory["newOrder"]
            order_payload = {
                "identifiers": [
                    {"type": "dns", "value": f"*.{clean_domain}"},
                    {"type": "dns", "value": clean_domain},
                ]
            }
            status, order_data, resp = await self._post(new_order_url, order_payload)
            if status not in (200, 201):
                raise RuntimeError(f"ACME new order failed: {order_data}")
            order_url = resp.headers.get("Location")
            authz_urls = order_data.get("authorizations", [])

            thumbprint = self._jwk_thumbprint()

            try:
                # 4. Handle Authorizations & DNS-01 challenges
                challenges_to_trigger: list[str] = []
                for auth_url in authz_urls:
                    status, auth_data, _ = await self._post(auth_url, "")
                    challenges = auth_data.get("challenges", [])
                    dns_chal = next((c for c in challenges if c.get("type") == "dns-01"), None)
                    if not dns_chal:
                        continue

                    token = dns_chal["token"]
                    key_auth = f"{token}.{thumbprint}".encode("ascii")
                    digest = hashes.Hash(hashes.SHA256())
                    digest.update(key_auth)
                    key_digest_b64 = _b64url(digest.finalize())

                    record_name = f"_acme-challenge.{clean_domain}"
                    rec_id = await self.cf_client.create_txt_record(zone_id, record_name, key_digest_b64)
                    if not rec_id:
                        raise RuntimeError(f"Failed to create Cloudflare TXT record for {clean_domain}")
                    created_records.append(rec_id)
                    challenges_to_trigger.append(dns_chal["url"])

                # Wait for Cloudflare DNS Anycast propagation
                logger.info("Waiting 15 seconds for Cloudflare DNS TXT propagation...")
                await asyncio.sleep(15.0)

                # 5. Tell ACME server challenge is ready
                for chal_url in challenges_to_trigger:
                    await self._post(chal_url, {})

                # 6. Poll order status until ready
                for _ in range(30):
                    await asyncio.sleep(2.0)
                    status, order_data, _ = await self._post(order_url, "")
                    order_status = order_data.get("status")
                    if order_status == "ready":
                        break
                    if order_status == "invalid":
                        raise RuntimeError(f"ACME order became invalid: {order_data}")

                # 7. Generate RSA 2048 certificate key and CSR
                cert_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
                csr = (
                    x509.CertificateSigningRequestBuilder()
                    .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"*.{clean_domain}")]))
                    .add_extension(
                        x509.SubjectAlternativeName([
                            x509.DNSName(f"*.{clean_domain}"),
                            x509.DNSName(clean_domain),
                        ]),
                        critical=False,
                    )
                    .sign(cert_key, hashes.SHA256())
                )
                csr_der = csr.public_bytes(serialization.Encoding.DER)

                # 8. Finalize order
                finalize_url = order_data["finalize"]
                status, order_data, _ = await self._post(finalize_url, {"csr": _b64url(csr_der)})

                # 9. Poll order status until valid
                for _ in range(30):
                    await asyncio.sleep(2.0)
                    status, order_data, _ = await self._post(order_url, "")
                    order_status = order_data.get("status")
                    if order_status == "valid":
                        break
                    if order_status == "invalid":
                        raise RuntimeError(f"ACME order finalization failed: {order_data}")

                # 10. Download certificate
                cert_url = order_data.get("certificate")
                if not cert_url:
                    raise RuntimeError("Missing certificate URL in finalized order")

                async with session.get(cert_url, headers={"Accept": "application/pem-certificate-chain"}) as resp:
                    if resp.status != 200:
                        raise RuntimeError(f"Certificate download failed with HTTP {resp.status}")
                    fullchain_pem = await resp.text()

                privkey_pem = cert_key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption(),
                ).decode("utf-8")

                logger.info("Successfully issued Wildcard certificate for *.%s and %s", clean_domain, clean_domain)
                return fullchain_pem, privkey_pem

            finally:
                # Clean up temporary DNS TXT records
                for rec_id in created_records:
                    await self.cf_client.delete_dns_record(zone_id, rec_id)


class WildcardCertificateManager:
    """Orchestrates certificate storage, auto-renewal, and fleet-wide deployment."""

    @staticmethod
    async def get_or_renew_certificate(session: AsyncSession) -> tuple[str, str] | None:
        """Fetch valid Wildcard certificate from database or automatically issue a new one."""
        settings = get_settings()
        cf_token = settings.CLOUDFLARE_API_TOKEN
        domain = settings.WILDCARD_DOMAIN
        if not cf_token or not domain:
            logger.debug("Cloudflare Wildcard is not configured (missing token or domain)")
            return None

        clean_domain = domain.strip().lower().lstrip("*.")
        cert_key_name = f"wildcard_fullchain_{clean_domain}"
        priv_key_name = f"wildcard_privkey_{clean_domain}"

        stored_fullchain = await get_system_setting(session, cert_key_name)
        stored_privkey = await get_system_setting(session, priv_key_name)

        if stored_fullchain and stored_privkey and is_certificate_valid(stored_fullchain, min_days=30):
            return stored_fullchain, stored_privkey

        logger.info("Wildcard certificate for %s needs issuance or renewal...", clean_domain)
        cf_client = CloudflareDnsClient(cf_token)
        account_key_pem = await get_system_setting(session, "acme_account_key_secp256r1")
        acme_client = AcmeDns01Client(
            directory_url=settings.ACME_DIRECTORY_URL,
            cf_client=cf_client,
            email=settings.SSL_EMAIL,
            account_key_pem=account_key_pem,
        )

        try:
            fullchain, privkey = await acme_client.issue_wildcard_certificate(clean_domain)
            await set_system_setting(session, cert_key_name, fullchain)
            await set_system_setting(session, priv_key_name, privkey)
            await set_system_setting(session, "acme_account_key_secp256r1", acme_client.export_account_key_pem())
            await session.commit()
            return fullchain, privkey
        except Exception as exc:
            logger.error("Wildcard issuance error for %s: %s", clean_domain, exc)
            return None
        finally:
            await cf_client.close()

    @staticmethod
    async def deploy_to_server(
        server: Server,
        fullchain_pem: str,
        privkey_pem: str,
        domain: str,
        node_client: XrayNodeClient | None = None,
    ) -> bool:
        """Deploy certificate to a specific server instance."""
        api_url = get_server_xray_api_url(server)
        if not api_url or not getattr(server, "api_key", None):
            return False

        owns_client = False
        if node_client is None:
            node_client = XrayNodeClient()
            owns_client = True

        try:
            success, err = await node_client.deploy_certificate(
                api_url=api_url,
                api_key=server.api_key,
                domain=domain,
                fullchain_pem=fullchain_pem,
                privkey_pem=privkey_pem,
                allow_bootstrap_tls=True,
            )
            if success:
                logger.info("Successfully deployed Wildcard certificate to server %s (%s)", server.id, server.name)
                return True
            logger.warning("Failed to deploy certificate to server %s (%s): %s", server.id, server.name, err)
            return False
        finally:
            if owns_client:
                await node_client.close()

    @classmethod
    async def deploy_to_all_active_nodes(cls, session: AsyncSession) -> dict[int, bool]:
        """Deploy current Wildcard certificate to all active nodes."""
        settings = get_settings()
        domain = settings.WILDCARD_DOMAIN
        if not domain:
            return {}

        cert_tuple = await cls.get_or_renew_certificate(session)
        if not cert_tuple:
            return {}

        fullchain, privkey = cert_tuple
        servers = await get_active_servers(session)
        results: dict[int, bool] = {}

        async with XrayNodeClient() as client:
            tasks = [
                cls.deploy_to_server(srv, fullchain, privkey, domain, node_client=client)
                for srv in servers
            ]
            if tasks:
                statuses = await asyncio.gather(*tasks, return_exceptions=True)
                for srv, st in zip(servers, statuses, strict=False):
                    results[srv.id] = (st is True)

        return results

    @classmethod
    async def deploy_to_server_id(cls, server_id: int) -> bool:
        """Fetch valid certificate and deploy to a specific server by ID."""
        settings = get_settings()
        domain = settings.WILDCARD_DOMAIN
        if not domain or not settings.CLOUDFLARE_API_TOKEN:
            return False

        from database.connection import session_scope

        try:
            async with session_scope() as session:
                server = await session.get(Server, server_id)
                if not server:
                    return False
                cert_tuple = await cls.get_or_renew_certificate(session)
                if not cert_tuple:
                    return False
                fullchain, privkey = cert_tuple
                return await cls.deploy_to_server(server, fullchain, privkey, domain)
        except Exception as exc:
            logger.warning("Error deploying wildcard TLS to server %s: %s", server_id, exc)
            return False

    @classmethod
    def deploy_to_server_id_background(cls, server_id: int) -> asyncio.Task:
        """Spawn background wildcard TLS deployment for a server with GC protection."""
        task = asyncio.create_task(cls.deploy_to_server_id(server_id))
        _BACKGROUND_TASKS.add(task)
        task.add_done_callback(_BACKGROUND_TASKS.discard)
        return task
