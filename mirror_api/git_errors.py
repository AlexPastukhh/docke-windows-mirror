"""Bounded Git failure categories; never return raw stderr or credential text."""
import re


def fetch_failure(stderr, returncode=None):
    raw = stderr[:16384] if isinstance(stderr, (str, bytes)) else ''
    message = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw
    patterns = (
        ('GIT_AUTH_REQUIRED', r'authentication failed|terminal prompts disabled|could not read (?:username|password)|permission denied \(publickey\)|logon failed'),
        ('GIT_TLS_FAILED', r'ssl certificate problem|certificate verify failed|schannel.*(?:certificate|trust)'),
        ('GIT_NETWORK_FAILED', r'could not resolve host|failed to connect|network is unreachable|connection (?:timed out|reset|refused)'),
        ('GIT_LOCKED', r'cannot lock ref|index\.lock|another git process|unable to create.*\.lock'),
        ('GIT_REMOTE_UNAVAILABLE', r'repository.*not found|could not read from remote repository'),
        ('GIT_LOCAL_ACCESS_DENIED', r'access is denied|unable to (?:create|open).*permission denied'),
    )
    cause = next((code for code, pattern in patterns if re.search(pattern, message, re.I)),
                 'GIT_FETCH_FAILURE_UNCLASSIFIED')
    code = returncode if type(returncode) is int and -(2**31) <= returncode < 2**32 else None
    return {'cause': cause, 'exitCode': code}
