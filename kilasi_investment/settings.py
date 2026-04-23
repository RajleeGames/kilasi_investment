"""
Django settings for kilasi_investment project.
VPS + Nginx ready for: kilasiinvestment.store
"""

import os
from pathlib import Path
from decouple import config
# ─── Paths ──────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent


# ─── Security ───────────────────────────────────────────────────
SECRET_KEY = 'django-insecure-rrep98zgpho^4hn2l=@ms&4vh$+e=yq=sh7)x=0wia_vx6(xe%)'

# IMPORTANT for VPS:
DEBUG = True

ALLOWED_HOSTS = [
    'kilasiinvestment.store',
    'www.kilasiinvestment.store',
    'localhost',
    '127.0.0.1',
]

# If you will use HTTPS (recommended), Django needs these trusted origins
CSRF_TRUSTED_ORIGINS = [
    'https://kilasiinvestment.store',
    'https://www.kilasiinvestment.store',
    # (Optional) while testing without SSL you can add http:
    'http://kilasiinvestment.store',
    'http://www.kilasiinvestment.store',
]

# Recommended behind Nginx (so Django knows requests were HTTPS)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')


# ─── Applications ───────────────────────────────────────────────
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    'inventory',
    'transport',
    'contacts',
    'sales',
    'users',
    'sms',
    'rangefilter',
    'topup',
]


# ─── Middleware ─────────────────────────────────────────────────
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]


ROOT_URLCONF = 'kilasi_investment.urls'


# ─── Templates ──────────────────────────────────────────────────
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [
            BASE_DIR / 'templates',
        ],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'transport.context_processors.transport_booking_badge',

                'sales.context_processors.debt_invoices_sidebar',
                'sales.context_processors.today_expenses',
            ],
        },
    },
]

WSGI_APPLICATION = 'kilasi_investment.wsgi.application'


# ─── Database ───────────────────────────────────────────────────
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}


# ─── Password Validators ────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]


BEEM_API_KEY = config('BEEM_API_KEY')
BEEM_SECRET_KEY = config('BEEM_SECRET_KEY')
BEEM_SENDER_ID = config('BEEM_SENDER_ID', default='KILASI LTD')

BEEM_API_URL_SEND = "https://apisms.beem.africa/v1/send"
BEEM_BALANCE_URL  = "https://apisms.beem.africa/public/v1/vendors/balance"
BEEM_DLR_URL      = "https://dlrapi.beem.africa/public/v1/delivery-reports"

# ─── Internationalization ───────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Africa/Dar_es_Salaam'
USE_I18N = True
USE_TZ = True


# ─── Static Files ───────────────────────────────────────────────
STATIC_URL = '/static/'

# Where collectstatic will place production static files
STATIC_ROOT = BASE_DIR / 'staticfiles'

# Your development static folder (keep if you have /static in project)
STATICFILES_DIRS = [
    BASE_DIR / 'static',
]


# ─── Media Files ────────────────────────────────────────────────
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'


# ─── Authentication ────────────────────────────────────────────
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'


# ─── Security Hardening (Safe defaults) ─────────────────────────
# IMPORTANT: Keep False until SSL is installed and working.
# After you install SSL with certbot, set this to True.
SECURE_SSL_REDIRECT = False

# Cookies over HTTPS only (turn True after SSL)
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Clickjacking / MIME sniff / referrer policy
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'

# HSTS (turn on after SSL testing, e.g. 31536000 for 1 year)
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False


# ─── Default Auto Field ─────────────────────────────────────────
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
