#!/usr/bin/env bash
# Creates .env.prod from the template and fills every secret that
# should be random. Safe to re-run: it only fills values that are still
# EMPTY, so it never overwrites a key that already protects data.
#
# If you are migrating the existing dev database to this server, set
# MFA_ENCRYPTION_KEY and PII_ENCRYPTION_KEY to the SAME values dev uses
# BEFORE restoring it — otherwise every enrolled MFA secret and every
# stored GST/PAN/TAN/IEC number becomes undecryptable.
. "$(dirname "$0")/lib.sh"

[ -f "$ENV_FILE" ] || cp deploy/.env.prod.example "$ENV_FILE"
chmod 600 "$ENV_FILE"

rand_hex()    { python3 -c "import secrets;print(secrets.token_hex(24))"; }
rand_url()    { python3 -c "import secrets;print(secrets.token_urlsafe(48))"; }
rand_fernet() { python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"; }

fill() { # KEY generator
  local key="$1" gen="$2"
  if grep -qE "^${key}=$" "$ENV_FILE"; then
    local v; v="$($gen)"
    sed -i "s|^${key}=$|${key}=${v}|" "$ENV_FILE"
    echo "  generated ${key}"
  else
    echo "  kept existing ${key}"
  fi
}
fill JWT_SECRET             rand_url
fill POSTGRES_PASSWORD      rand_hex
fill DOI_APP_PASSWORD       rand_hex
fill MFA_ENCRYPTION_KEY     rand_fernet
fill PII_ENCRYPTION_KEY     rand_fernet
fill BACKUP_ENCRYPTION_KEY  rand_fernet

cat <<MSG

$ENV_FILE is ready (mode 600). NOW:
  1. Fill the remaining blanks by hand (DOMAIN, ACME_EMAIL, SMTP_*, R2_*, SIGNUP_ALLOWLIST...).
  2. Copy the WHOLE file into your password manager. Losing
     BACKUP_ENCRYPTION_KEY makes every backup unrestorable; losing
     MFA_ENCRYPTION_KEY / PII_ENCRYPTION_KEY makes that data unreadable.
     They exist nowhere else.
MSG
