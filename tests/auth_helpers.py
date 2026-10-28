import hashlib


def verified(app):
    """Explicit test-only fixture; production code has no credential defaults."""
    app.session_state['wcl_client_id']='test-client'
    app.session_state['wcl_client_secret']='test-secret'
    app.session_state['_verified_credentials']=hashlib.sha256(b'test-client:test-secret').hexdigest()
    return app
