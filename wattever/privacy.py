"""Conservative, recursive projection for files intended for public sharing.

This removes recognizable machine identifiers and connection secrets. It cannot
identify every secret in arbitrary prose; labels and notes still need review.
"""
import re
import copy
from urllib.parse import urlsplit


LOCAL_DETAIL = re.compile(r"(?:\b[A-Za-z]:[\\/]|\\\\[^\\\s]+\\|/(?:Users|home|root|tmp|private|mnt|media|var|opt)/|(?:^|\s)/(?:[^\s/]+/)+[^\s]+|file://|\bWinError\s*\d+)", re.I)
NETWORK_DETAIL = re.compile(r"(?:https?://[^\s<>\"']+|\b(?:\d{1,3}\.){3}\d{1,3}\b|\[[0-9a-f:]*:[0-9a-f:]*\]|(?<![0-9a-z])(?:[0-9a-f]{0,4}:){3,}[0-9a-f]{0,4}(?:%[\w.-]+)?|\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b|\b(?:Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+|\bsk-[A-Za-z0-9_-]{12,})", re.I)
DIAGNOSTIC_KEYS = {"error", "message", "log", "traceback", "exception", "stderr", "stdout"}
PRIVATE_KEYS = {
    "authorization", "proxyauthorization", "auth", "authentication", "bearer", "apikey", "accesstoken", "refreshtoken", "key", "accesskey", "privatekey",
    "password", "passwd", "secret", "clientsecret", "credentials", "credential", "token", "authtoken", "hftoken", "user",
    "cookie", "setcookie", "headers", "requestheaders", "responseheaders",
    "environment", "environ", "env", "endpoint", "endpointurl", "baseurl", "serverurl",
    "hostname", "hostaddress", "serveraddress", "ip", "ipaddress", "ipv4", "ipv6", "macaddress", "username",
    "userprofile", "computername", "machinename", "serial", "serialnumber", "uuid",
    "deviceuuid", "gpuuuid", "machineid", "deviceid", "connection", "connectionconfig",
    "storageprobedirectory", "storageprobedirectories",
}


def private_key(key):
    normalized = re.sub(r"[^a-z0-9]", "", key.lower())
    return (normalized in PRIVATE_KEYS or re.search(r"(?:^|_)(?:TOKEN|PASSWORD|SECRET|API_KEY)$", key, re.I) or
            any(normalized.endswith(suffix) for suffix in
                ("apikey", "password", "passwd", "secret", "accesstoken", "refreshtoken",
                 "authorization", "serialnumber", "deviceuuid", "gpuuuid", "endpointurl", "baseurl",
                 "apitoken", "authtoken", "sessiontoken", "idtoken", "apikeyenv")))


def public_source(value):
    """An explicitly supplied public citation may retain its public DNS URL."""
    if not isinstance(value, str):
        return False
    if re.search(r"\s|\b(?:Bearer|Basic)\b|\bsk-[A-Za-z0-9_-]{12,}", value, re.I):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        return bool(parsed.scheme in ("http", "https") and
                    re.fullmatch(r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}", host) and
                    not host.endswith((".local", ".localhost", ".internal", ".test", ".invalid", ".example", ".lan", ".home", ".corp")) and
                    not (parsed.username or parsed.password or parsed.query or parsed.fragment))
    except ValueError:
        return False


def public_projection(value, _path=()):
    """Copy shareable measurements, omitting recognizable private information."""
    if isinstance(value, list):
        return [public_projection(item, _path) for item in value]
    if isinstance(value, dict):
        # A validated public configuration contains an endpoint route *label*,
        # not transport addresses. Preserve its exact fingerprint-bearing form.
        if value.get("fingerprint_version") == "wattever-model-configuration-v1":
            from .identity import verify_model_configuration
            try:
                verify_model_configuration(value, require_endpoint=False)
                return copy.deepcopy(value)
            except (ValueError, TypeError, KeyError):
                pass
        result = {}
        redacted = storage_access = False
        for key, item in value.items():
            if key in ("model", "response_model") and isinstance(item, str) and item.startswith(("/", "\\")):
                result[key] = "private model identifier"
                redacted = True
                continue
            if (LOCAL_DETAIL.search(key) or NETWORK_DETAIL.search(key) or private_key(key) or
                    (key.lower() == "host" and isinstance(item, str))):
                redacted = True
                continue
            if key.lower() in DIAGNOSTIC_KEYS:
                redacted = True
                if re.search(r"\bWinError\s*5\b", str(item), re.I):
                    storage_access = True
                continue
            if key == "source" and "performance_claims" in _path and public_source(item):
                result[key] = item
                continue
            if isinstance(item, str) and LOCAL_DETAIL.search(item):
                result[key] = "[local detail redacted]"
                redacted = True
                if re.search(r"\bWinError\s*5\b", item, re.I):
                    storage_access = True
            elif isinstance(item, str) and NETWORK_DETAIL.search(item):
                result[key] = "[network or credential detail redacted]"
                redacted = True
            else:
                result[key] = public_projection(item, _path + (key,))
        if redacted:
            result["diagnostic_redacted"] = True
        if storage_access:
            result["diagnostic_category"] = "storage_access"
        return result
    if isinstance(value, str):
        if LOCAL_DETAIL.search(value):
            return "[local detail redacted]"
        if NETWORK_DETAIL.search(value):
            return "[network or credential detail redacted]"
    return value
