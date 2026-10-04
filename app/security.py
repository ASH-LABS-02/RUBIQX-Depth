"""Optional local auth, mandatory production auth, and same-origin mutation checks."""
import base64
import hmac
import os
from urllib.parse import urlsplit
from fastapi.responses import JSONResponse


def credentials():
    user=os.environ.get('DEPTHWIZARD_AUTH_USER','')
    password=os.environ.get('DEPTHWIZARD_AUTH_PASSWORD','')
    production=os.environ.get('DEPTHWIZARD_PRODUCTION','0')=='1'
    if production and (not user or len(password)<16):
        raise RuntimeError('Production mode requires DEPTHWIZARD_AUTH_USER and a password of at least 16 characters')
    if bool(user)!=bool(password):
        raise RuntimeError('Set both DEPTHWIZARD_AUTH_USER and DEPTHWIZARD_AUTH_PASSWORD')
    return user,password


def install_security(app):
    user,password=credentials()
    @app.middleware('http')
    async def guard(request,call_next):
        if user and request.url.path!='/api/health':
            supplied=request.headers.get('authorization','')
            expected='Basic '+base64.b64encode((user+':'+password).encode()).decode()
            if not hmac.compare_digest(supplied,expected):
                return JSONResponse({'detail':'Authentication required'},401,headers={'WWW-Authenticate':'Basic realm="DepthWizard", charset="UTF-8"'})
        if request.method in ('POST','PUT','PATCH','DELETE'):
            origin=request.headers.get('origin')
            if origin and urlsplit(origin).netloc.lower()!=request.headers.get('host','').lower():
                return JSONResponse({'detail':'Cross-origin changes are not allowed'},403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['X-Frame-Options']='SAMEORIGIN'
        return response
