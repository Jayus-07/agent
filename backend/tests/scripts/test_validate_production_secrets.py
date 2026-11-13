"""生产预检只接受能解开现有供应商密文的主钥匙。"""

from cryptography.fernet import Fernet

from backend.scripts.validate_production_secrets import unreadable_provider_ids


def test_secret_preflight_finds_credentials_encrypted_with_another_key():
    active_key = Fernet.generate_key()
    old_key = Fernet.generate_key()
    rows = [
        {
            "provider_id": "provider-current",
            "key_cipher": "enc:" + Fernet(active_key).encrypt(b"api-key").decode(),
        },
        {
            "provider_id": "provider-old",
            "key_cipher": "enc:" + Fernet(old_key).encrypt(b"old-api-key").decode(),
        },
        {"provider_id": "provider-plain", "key_cipher": "legacy-plain-value"},
    ]

    assert unreadable_provider_ids(rows, active_key.decode()) == ["provider-old"]


def test_secret_preflight_sanitizes_provider_ids():
    active_key = Fernet.generate_key()
    wrong_key = Fernet.generate_key()
    rows = [{
        "provider_id": "provider\nunsafe",
        "key_cipher": "enc:" + Fernet(wrong_key).encrypt(b"api-key").decode(),
    }]

    assert unreadable_provider_ids(rows, active_key.decode()) == [
        "provider?unsafe"
    ]
