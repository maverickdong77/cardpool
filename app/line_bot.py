import os
import re
import json
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
    FlexMessage,
    FlexContainer,
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from app.scraper.ebay import get_ebay_prices
from app.scraper.snkrdunk import get_snkrdunk_prices
from app.database import (
    get_card_by_psa,
    search_cards_by_name,
    get_latest_prices,
    save_search_history,
    save_card,
    save_ebay_price,
    save_snkrdunk_price,
)


def get_line_api() -> MessagingApi:
    """取得 LINE Messaging API 客戶端"""
    configuration = Configuration(
        access_token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
    )
    return MessagingApi(ApiClient(configuration))


def is_psa_cert_number(text: str) -> bool:
    """判斷是否為 PSA 認證編號（通常是 8-10 位數字）"""
    cleaned = text.strip()
    return bool(re.match(r"^\d{7,10}$", cleaned))


async def handle_message(event: MessageEvent):
    """處理使用者訊息"""
    if not isinstance(event.message, TextMessageContent):
        return

    user_id = event.source.user_id
    user_text = event.message.text.strip()
    reply_token = event.reply_token

    line_api = get_line_api()

    # 判斷搜尋類型
    if is_psa_cert_number(user_text):
        # PSA 編號搜尋
        await save_search_history(user_id, user_text, "psa_cert")
        messages = await search_by_psa(user_text)
    elif user_text.startswith("/"):
        # 指令處理
        messages = [TextMessage(text=handle_command(user_text))]
    else:
        # 卡片名稱搜尋
        await save_search_history(user_id, user_text, "card_name")
        messages = await search_by_name(user_text)

    # 回覆訊息
    line_api.reply_message(
        ReplyMessageRequest(
            reply_token=reply_token,
            messages=messages
        )
    )


async def search_by_psa(cert_number: str) -> list:
    """用 PSA 編號搜尋"""
    card = await get_card_by_psa(cert_number)

    ebay_results = await get_ebay_prices(cert_number, is_cert=True)
    snkrdunk_results = await get_snkrdunk_prices(cert_number, is_cert=True)

    # 儲存價格資料
    for result in ebay_results[:3]:
        await save_ebay_price({
            "psa_cert_number": cert_number,
            "price_usd": result["price_usd"],
            "price_twd": result["price_twd"],
            "sale_date": result["sale_date"],
            "listing_title": result["listing_title"],
            "listing_url": result["listing_url"],
        })

    return create_flex_response(cert_number, ebay_results, snkrdunk_results)


async def search_by_name(card_name: str) -> list:
    """用卡片名稱搜尋"""
    ebay_results = await get_ebay_prices(card_name, is_cert=False)
    snkrdunk_results = await get_snkrdunk_prices(card_name, is_cert=False)

    if not ebay_results and not snkrdunk_results:
        return [TextMessage(text=f"找不到「{card_name}」的相關價格資料\n\n請嘗試：\n• 使用英文名稱搜尋\n• 輸入更具體的卡片名稱\n• 直接輸入 PSA 認證編號")]

    return create_flex_response(card_name, ebay_results, snkrdunk_results)


def create_flex_response(query: str, ebay_results: list, snkrdunk_results: list) -> list:
    """建立 Flex Message 回覆（卡片輪播）"""

    # 合併結果
    all_results = ebay_results[:5]  # 最多取 5 筆

    if not all_results:
        return [TextMessage(text=f"找不到「{query}」的相關價格資料")]

    # 建立卡片輪播
    bubbles = []
    for result in all_results:
        bubble = create_card_bubble(result)
        bubbles.append(bubble)

    # 加入統計卡片
    if ebay_results:
        stats_bubble = create_stats_bubble(query, ebay_results)
        bubbles.append(stats_bubble)

    carousel = {
        "type": "carousel",
        "contents": bubbles
    }

    flex_message = FlexMessage(
        alt_text=f"查詢結果：{query}",
        contents=FlexContainer.from_dict(carousel)
    )

    return [flex_message]


def create_card_bubble(result: dict) -> dict:
    """建立單張卡片的 Bubble"""
    title = result.get("listing_title", "Unknown")
    # 清理標題
    title = title.split('\n')[0]
    title = title[:50] + '...' if len(title) > 50 else title

    price_usd = result.get("price_usd", 0)
    price_twd = result.get("price_twd", 0)
    image_url = result.get("image_url")
    listing_url = result.get("listing_url", "https://www.ebay.com")

    # 預設圖片
    if not image_url or image_url.startswith("data:"):
        image_url = "https://i.ebayimg.com/images/g/placeholder.jpg"

    bubble = {
        "type": "bubble",
        "size": "kilo",
        "hero": {
            "type": "image",
            "url": image_url,
            "size": "full",
            "aspectRatio": "1:1",
            "aspectMode": "cover",
            "action": {
                "type": "uri",
                "uri": listing_url
            }
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "size": "sm",
                    "wrap": True,
                    "maxLines": 2
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "spacing": "sm",
                    "contents": [
                        {
                            "type": "text",
                            "text": f"US${price_usd:,.0f}",
                            "size": "xl",
                            "weight": "bold",
                            "color": "#1DB446"
                        },
                        {
                            "type": "text",
                            "text": f"≈ NT${price_twd:,.0f}",
                            "size": "sm",
                            "color": "#999999"
                        }
                    ]
                }
            ]
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "height": "sm",
                    "action": {
                        "type": "uri",
                        "label": "查看詳情",
                        "uri": listing_url
                    }
                }
            ]
        }
    }

    return bubble


def create_stats_bubble(query: str, results: list) -> dict:
    """建立統計摘要卡片"""
    prices = [r["price_usd"] for r in results[:5]]
    avg_price = sum(prices) / len(prices)
    min_price = min(prices)
    max_price = max(prices)
    avg_twd = avg_price * 32

    bubble = {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "📊 價格統計",
                    "weight": "bold",
                    "size": "lg"
                },
                {
                    "type": "text",
                    "text": query,
                    "size": "sm",
                    "color": "#999999",
                    "margin": "md"
                },
                {
                    "type": "separator",
                    "margin": "lg"
                },
                {
                    "type": "box",
                    "layout": "vertical",
                    "margin": "lg",
                    "spacing": "md",
                    "contents": [
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "平均價格", "size": "sm", "color": "#555555", "flex": 0},
                                {"type": "text", "text": f"US${avg_price:,.0f}", "size": "sm", "color": "#111111", "align": "end", "weight": "bold"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "台幣約", "size": "sm", "color": "#555555", "flex": 0},
                                {"type": "text", "text": f"NT${avg_twd:,.0f}", "size": "sm", "color": "#1DB446", "align": "end", "weight": "bold"}
                            ]
                        },
                        {
                            "type": "separator",
                            "margin": "sm"
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "最低", "size": "xs", "color": "#999999", "flex": 0},
                                {"type": "text", "text": f"US${min_price:,.0f}", "size": "xs", "color": "#999999", "align": "end"}
                            ]
                        },
                        {
                            "type": "box",
                            "layout": "horizontal",
                            "contents": [
                                {"type": "text", "text": "最高", "size": "xs", "color": "#999999", "flex": 0},
                                {"type": "text", "text": f"US${max_price:,.0f}", "size": "xs", "color": "#999999", "align": "end"}
                            ]
                        },
                        {
                            "type": "text",
                            "text": f"共 {len(results)} 筆成交紀錄",
                            "size": "xs",
                            "color": "#AAAAAA",
                            "margin": "lg",
                            "align": "center"
                        }
                    ]
                }
            ]
        }
    }

    return bubble


def handle_command(text: str) -> str:
    """處理指令"""
    cmd = text.lower().split()[0]

    if cmd == "/help":
        return """📖 使用說明

🔍 查詢方式：
• 輸入 PSA 編號（如：12345678）
• 輸入卡片名稱（如：Pikachu VMAX）

📝 指令：
• /help - 顯示說明
• /about - 關於本服務

💡 小提示：
• 英文名稱搜尋結果較準確
• 輸入更具體名稱如「pikachu vmax」
• PSA 編號可直接查詢精確價格"""

    elif cmd == "/about":
        return """🔍 Cardpool Price Searching

提供 PSA 鑑定卡價格查詢服務
資料來源：eBay、SNKRDUNK

⚠️ 價格僅供參考，實際成交價可能有所不同"""

    else:
        return f"未知指令：{cmd}\n輸入 /help 查看使用說明"
