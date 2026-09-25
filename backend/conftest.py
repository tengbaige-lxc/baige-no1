import os


# Tests must never depend on production credentials or service.env.
os.environ.setdefault("SECRET_KEY", "test-only-secret-key-that-is-longer-than-thirty-two-characters")
os.environ.setdefault("EXCHANGE_KEY_SECRET", "test-only-exchange-key-secret")
