import os
import json
import hashlib
import time
import requests
import xmltodict
from fastapi import FastAPI, Request, Query
from fastapi.responses import PlainTextResponse
from openai import OpenAI

app = FastAPI()

# ── 配置 ──
CORP_ID       = os.getenv("CORP_ID")
CORP_SECRET   = os.getenv("CORP_SECRET")
TOKEN         = os.getenv("WECHAT_TOKEN")
AES_KEY       = os.getenv("WECHAT_AES_KEY")
DS_API_KEY    = os.getenv("DEEPSEEK_API_KEY")
DS_BASE_URL   = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
DS_MODEL      = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

# ── DeepSeek 客户端 ──
ds_client = OpenAI(api_key=DS_API_KEY, base_url=DS_BASE_URL)

# ── 会话历史（内存，重启清空） ──
chat_history = {}

SYSTEM_PROMPT = """你是一个友好、专业的AI助手，名字叫小智。
用自然、简洁的中文回答用户问题。
每次回复控制在200字以内，重点突出，避免废话。"""


def get_access_token():
    """获取企业微信access_token"""
    url = f"https://qyapi.weixin.qq.com/cgi-bin/gettoken?corpid={CORP_ID}&corpsecret={CORP_SECRET}"
    r = requests.get(url, timeout=10)
    return r.json().get("access_token")


def send_wechat_message(open_kf_id: str, external_userid: str, content: str):
    """发送消息给微信客服用户"""
    token = get_access_token()
    url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={token}"
    payload = {
        "touser": external_userid,
        "open_kfid": open_kf_id,
        "msgtype": "text",
        "text": {"content": content}
    }
    r = requests.post(url, json=payload, timeout=10)
    return r.json()


def get_kf_messages(token: str, cursor: str = ""):
    """拉取微信客服消息"""
    url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?access_token={token}"
    payload = {"cursor": cursor, "token": token, "limit": 1000}
    r = requests.post(url, json=payload, timeout=10)
    return r.json()


def chat_with_deepseek(user_id: str, user_message: str) -> str:
    """调用DeepSeek API获取回复"""
    if user_id not in chat_history:
        chat_history[user_id] = []

    chat_history[user_id].append({"role": "user", "content": user_message})

    # 保留最近20条
    if len(chat_history[user_id]) > 20:
        chat_history[user_id] = chat_history[user_id][-20:]

    try:
        response = ds_client.chat.completions.create(
            model=DS_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + chat_history[user_id],
            max_tokens=512,
            temperature=0.7
        )
        reply = response.choices[0].message.content
        chat_history[user_id].append({"role": "assistant", "content": reply})
        return reply
    except Exception as e:
        return f"抱歉，AI暂时无法响应，请稍后再试。({str(e)[:50]})"


def verify_signature(signature: str, timestamp: str, nonce: str) -> bool:
    """验证企业微信回调签名"""
    params = sorted([TOKEN, timestamp, nonce])
    string = "".join(params)
    return hashlib.sha1(string.encode()).hexdigest() == signature


# ── 路由 ──

@app.get("/")
async def root():
    return {"status": "ok", "service": "Hitalk AI客服"}


@app.get("/wechat/callback")
async def verify_callback(
    msg_signature: str = Query(""),
    timestamp: str = Query(""),
    nonce: str = Query(""),
    echostr: str = Query("")
):
    """企业微信验证回调URL"""
    if verify_signature(msg_signature, timestamp, nonce):
        return PlainTextResponse(echostr)
    return PlainTextResponse("验证失败", status_code=403)


@app.post("/wechat/callback")
async def receive_callback(request: Request):
    """接收企业微信推送的消息事件"""
    body = await request.body()

    try:
        data = xmltodict.parse(body)
        event = data.get("xml", {})
        msg_type = event.get("MsgType", "")
        event_type = event.get("Event", "")

        # 只处理微信客服消息事件
        if event_type == "kf_msg_or_event":
            token = get_access_token()
            result = get_kf_messages(token)
            msg_list = result.get("msg_list", [])

            for msg in msg_list:
                if msg.get("msgtype") != "text":
                    continue
                user_id    = msg.get("external_userid", "")
                open_kf_id = msg.get("open_kfid", "")
                content    = msg.get("text", {}).get("content", "").strip()

                if not content or not user_id:
                    continue

                print(f"[收到] {user_id}: {content}")
                reply = chat_with_deepseek(user_id, content)
                print(f"[回复] {reply}")
                send_wechat_message(open_kf_id, user_id, reply)

    except Exception as e:
        print(f"[错误] {e}")

    return PlainTextResponse("success")
