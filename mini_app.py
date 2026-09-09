
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import uuid
from datetime import datetime
from urllib.parse import parse_qsl

from flask import jsonify, request, send_from_directory


_EVENT_LOOP = None
_REGISTERED = False


def set_mini_app_event_loop(loop):
    global _EVENT_LOOP
    _EVENT_LOOP = loop


def _run_async(coro, timeout=65):
    if _EVENT_LOOP is None:
        raise RuntimeError("Mini App event loop is not ready")
    import asyncio
    future = asyncio.run_coroutine_threadsafe(coro, _EVENT_LOOP)
    return future.result(timeout=timeout)


def _json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _error(message, status=400):
    return jsonify({"ok": False, "error": message}), status


def _hash_token(token):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _validate_telegram_init_data(init_data, bot_token, max_age=86400):
    if not init_data or not bot_token:
        return None, "Telegram session data is missing"
    try:
        values = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = values.pop("hash", "")
        if not received_hash:
            return None, "Telegram session signature is missing"
        data_check_string = "\\n".join(
            f"{key}={value}" for key, value in sorted(values.items())
        )
        secret_key = hmac.new(
            b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256
        ).digest()
        expected_hash = hmac.new(
            secret_key, data_check_string.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash):
            return None, "Telegram session signature is invalid"
        auth_date = int(values.get("auth_date", "0"))
        if not auth_date or time.time() - auth_date > max_age:
            return None, "Telegram session has expired"
        user = json.loads(values.get("user", "{}"))
        user_id = int(user["id"])
        return {"user_id": user_id, "user": user}, None
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None, "Telegram session data is invalid"


def _bearer_token():
    value = request.headers.get("Authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def register_mini_app(
    app,
    *,
    bot_token,
    users_col,
    deposits_col,
    withdrawals_col,
    mini_sessions_col,
    mini_deposit_intents_col,
    get_system_wallet_address,
    get_user_profile,
    is_valid_ton_address,
    normalize_ton_address,
    reserve_user_balance,
    create_withdrawal_record,
    set_withdrawal_status,
    refund_withdrawal,
    send_ton_payout,
    min_deposit_amount,
    min_withdraw_amount,
    max_withdraw_amount,
    ton_gas_fee,
    withdrawals_enabled_getter,
):
    global _REGISTERED
    if _REGISTERED:
        return
    _REGISTERED = True
    static_dir = os.path.dirname(os.path.abspath(__file__))

    async def create_session(user):
        token = secrets.token_urlsafe(32)
        now = time.time()
        await mini_sessions_col.delete_many({"expires_at": {"$lte": now}})
        await mini_sessions_col.insert_one({
            "token_hash": _hash_token(token),
            "user_id": int(user["user_id"]),
            "telegram_user": user.get("user", {}),
            "created_at": now,
            "expires_at": now + 86400,
        })
        return token

    async def get_session_user(token):
        if not token:
            return None
        session = await mini_sessions_col.find_one({
            "token_hash": _hash_token(token),
            "expires_at": {"$gt": time.time()},
        })
        return int(session["user_id"]) if session else None

    async def get_state(user_id):
        user = await users_col.find_one({"user_id": user_id}) or {}
        return {
            "user_id": user_id,
            "balance": round(float(user.get("balance", 0.0)), 4),
            "wallet_address": user.get("wallet_address", ""),
            "verified_wallet_address": user.get("verified_wallet_address", ""),
            "wallet_name": user.get("wallet_name", ""),
            "min_deposit": float(min_deposit_amount),
            "min_withdraw": float(min_withdraw_amount),
            "max_withdraw": float(max_withdraw_amount),
            "network_fee": float(max(ton_gas_fee, 0)),
            "withdrawals_enabled": bool(withdrawals_enabled_getter()),
        }

    async def process_withdrawal(user_id, address, requested_amount):
        state = await get_state(user_id)
        if not state["withdrawals_enabled"]:
            return {"ok": False, "error": "برداشت موقتاً غیرفعال است."}
        try:
            requested_amount = round(float(requested_amount), 4)
        except (TypeError, ValueError):
            return {"ok": False, "error": "مبلغ برداشت معتبر نیست."}
        if requested_amount < float(min_withdraw_amount) or requested_amount > float(max_withdraw_amount):
            return {"ok": False, "error": "مبلغ برداشت خارج از محدوده مجاز است."}
        if requested_amount <= max(float(ton_gas_fee), 0):
            return {"ok": False, "error": "مبلغ باید از کارمزد شبکه بیشتر باشد."}
        address = normalize_ton_address(str(address or "").strip())
        if not is_valid_ton_address(address):
            return {"ok": False, "error": "آدرس کیف‌پول TON معتبر نیست."}
        connected = normalize_ton_address(str(state.get("wallet_address") or "").strip())
        verified = normalize_ton_address(str(state.get("verified_wallet_address") or "").strip())
        if not connected or connected != address:
            return {"ok": False, "error": "این آدرس با کیف‌پول متصل‌شده یکسان نیست."}
        if not verified or verified != address:
            return {"ok": False, "error": "برای برداشت، ابتدا حداقل یک واریز موفق از همین کیف‌پول انجام دهید."}

        amount_to_send = round(requested_amount - max(float(ton_gas_fee), 0), 4)
        withdrawal_id = await create_withdrawal_record(
            user_id,
            address,
            requested_amount,
            amount_to_send,
            requested_amount,
            asset="TON",
            fee_ton=float(max(ton_gas_fee, 0)),
        )
        reserved = False
        try:
            reserved = await reserve_user_balance(user_id, requested_amount)
            if not reserved:
                await set_withdrawal_status(
                    withdrawal_id, "failed", last_error="موجودی TON کافی نیست"
                )
                return {"ok": False, "error": "موجودی TON برای این برداشت کافی نیست."}
            await set_withdrawal_status(
                withdrawal_id,
                "processing",
                reserved_at=datetime.utcnow().isoformat(),
                source="telegram_mini_app",
            )
            payout_status, result_message = await send_ton_payout(address, amount_to_send)
            if payout_status == "sent":
                await set_withdrawal_status(
                    withdrawal_id,
                    "sent",
                    sent_at=datetime.utcnow().isoformat(),
                    result_message=result_message,
                )
                return {
                    "ok": True,
                    "status": "sent",
                    "withdrawal_id": withdrawal_id,
                    "amount_sent": amount_to_send,
                    "message": "برداشت با موفقیت به کیف‌پول متصل‌شده ارسال شد.",
                }
            if payout_status == "uncertain":
                await set_withdrawal_status(
                    withdrawal_id,
                    "pending_verification",
                    last_error=result_message,
                    verification_required_at=datetime.utcnow().isoformat(),
                )
                return {
                    "ok": True,
                    "status": "pending_verification",
                    "withdrawal_id": withdrawal_id,
                    "message": "تراکنش ارسال شده اما تأیید نهایی شبکه هنوز دریافت نشده است.",
                }
            await set_withdrawal_status(
                withdrawal_id, "failed", last_error=result_message
            )
            refunded = await refund_withdrawal(
                withdrawal_id,
                result_message,
                allowed_statuses=("failed",),
            )
            return {
                "ok": False,
                "status": "failed",
                "withdrawal_id": withdrawal_id,
                "refunded": bool(refunded),
                "error": "برداشت انجام نشد و در صورت رزرو، مبلغ آزاد شد.",
            }
        except Exception as exc:
            logging.exception("Mini App TON withdrawal failed")
            await set_withdrawal_status(
                withdrawal_id, "failed", last_error=str(exc)
            )
            if reserved:
                await refund_withdrawal(
                    withdrawal_id,
                    "خطای داخلی در برداشت Mini App",
                    allowed_statuses=("failed",),
                )
            return {"ok": False, "error": "خطای داخلی در پردازش برداشت."}

    @app.get("/mini-app")
    def mini_app_page():
        return send_from_directory(static_dir, "mini_app.html")

    @app.get("/mini-app-icon.svg")
    def mini_app_icon():
        return send_from_directory(static_dir, "mini_app_icon.svg", mimetype="image/svg+xml")

    @app.get("/tonconnect-manifest.json")
    def tonconnect_manifest():
        base_url = os.environ.get("MINI_APP_URL", "").rstrip("/")
        if not base_url.startswith("https://"):
            return _error("MINI_APP_URL must be configured with an HTTPS URL", 503)
        return jsonify({
            "url": base_url + "/mini-app",
            "name": "Void Giveaway",
            "iconUrl": base_url + "/mini-app-icon.svg",
        })

    @app.post("/api/mini-app/session")
    def mini_app_session():
        data = _json_body()
        validated, error = _validate_telegram_init_data(
            data.get("init_data", ""), bot_token
        )
        if error:
            return _error(error, 401)
        token = _run_async(create_session(validated))
        return jsonify({"ok": True, "token": token, "user_id": validated["user_id"]})

    @app.get("/api/mini-app/state")
    def mini_app_state():
        user_id = _run_async(get_session_user(_bearer_token()))
        if not user_id:
            return _error("Mini App session is missing or expired", 401)
        return jsonify({"ok": True, "state": _run_async(get_state(user_id))})

    @app.post("/api/mini-app/wallet")
    def connect_wallet():
        user_id = _run_async(get_session_user(_bearer_token()))
        if not user_id:
            return _error("Mini App session is missing or expired", 401)
        data = _json_body()
        address = normalize_ton_address(str(data.get("address", "")).strip())
        if not is_valid_ton_address(address):
            return _error("آدرس کیف‌پول TON معتبر نیست.")
        wallet_name = str(data.get("wallet_name", ""))[:80]
        _run_async(users_col.update_one(
            {"user_id": user_id},
            {"$set": {
                "user_id": user_id,
                "wallet_address": address,
                "wallet_name": wallet_name,
                "wallet_connected_at": datetime.utcnow().isoformat(),
            }},
            upsert=True,
        ))
        return jsonify({"ok": True, "address": address, "state": _run_async(get_state(user_id))})

    @app.delete("/api/mini-app/wallet")
    def disconnect_wallet():
        user_id = _run_async(get_session_user(_bearer_token()))
        if not user_id:
            return _error("Mini App session is missing or expired", 401)
        _run_async(users_col.update_one(
            {"user_id": user_id},
            {"$unset": {"wallet_address": "", "wallet_name": "", "wallet_connected_at": ""}},
        ))
        return jsonify({"ok": True})

    @app.post("/api/mini-app/deposit-intents")
    def create_deposit_intent():
        user_id = _run_async(get_session_user(_bearer_token()))
        if not user_id:
            return _error("Mini App session is missing or expired", 401)
        data = _json_body()
        try:
            amount = round(float(data.get("amount")), 4)
        except (TypeError, ValueError):
            return _error("مبلغ واریز معتبر نیست.")
        if amount < float(min_deposit_amount):
            return _error("مبلغ کمتر از حداقل واریز است.")
        state = _run_async(get_state(user_id))
        if not state.get("wallet_address"):
            return _error("ابتدا یک کیف‌پول را متصل کنید.")
        destination = _run_async(get_system_wallet_address())
        if not destination:
            return _error("کیف‌پول مرکزی موقتاً در دسترس نیست.", 503)
        intent_id = uuid.uuid4().hex[:16]
        memo = f"VG-{user_id}-{intent_id}"
        amount_nano = int(round(amount * 10**9))
        _run_async(mini_deposit_intents_col.insert_one({
            "intent_id": intent_id,
            "user_id": user_id,
            "wallet_address": state["wallet_address"],
            "amount_requested": amount,
            "amount_nano": amount_nano,
            "destination": destination,
            "memo": memo,
            "status": "pending",
            "created_at": datetime.utcnow().isoformat(),
        }))
        return jsonify({
            "ok": True,
            "intent_id": intent_id,
            "destination": destination,
            "amount": amount,
            "amount_nano": amount_nano,
            "memo": memo,
        })

    @app.get("/api/mini-app/deposit-intents/<intent_id>")
    def deposit_intent_status(intent_id):
        user_id = _run_async(get_session_user(_bearer_token()))
        if not user_id:
            return _error("Mini App session is missing or expired", 401)
        intent = _run_async(mini_deposit_intents_col.find_one({
            "intent_id": intent_id,
            "user_id": user_id,
        }))
        if not intent:
            return _error("واریز پیدا نشد.", 404)
        intent.pop("_id", None)
        return jsonify({"ok": True, "intent": intent})

    @app.post("/api/mini-app/withdrawals")
    def mini_app_withdrawal():
        user_id = _run_async(get_session_user(_bearer_token()))
        if not user_id:
            return _error("Mini App session is missing or expired", 401)
        data = _json_body()
        state = _run_async(get_state(user_id))
        address = normalize_ton_address(str(data.get("address") or state.get("wallet_address") or ""))
        result = _run_async(process_withdrawal(user_id, address, data.get("amount")), timeout=90)
        status = 200 if result.get("ok") else (409 if result.get("status") == "failed" else 400)
        return jsonify(result), status

    @app.get("/api/mini-app/withdrawals")
    def mini_app_withdrawals():
        user_id = _run_async(get_session_user(_bearer_token()))
        if not user_id:
            return _error("Mini App session is missing or expired", 401)
        rows = _run_async(
            withdrawals_col.find({"user_id": user_id, "source": "telegram_mini_app"})
            .sort("created_at", -1)
            .limit(10)
            .to_list(length=10)
        )
        for row in rows:
            row.pop("_id", None)
        return jsonify({"ok": True, "withdrawals": rows})

    logging.info("TON Connect Mini App routes registered")
