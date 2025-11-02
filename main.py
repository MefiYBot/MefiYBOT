import discord
from discord.ext import commands, tasks
from discord import app_commands, ui, utils
import os
import uuid
from supabase import create_client, Client
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import datetime

# .envファイルを読み込む
load_dotenv()

# --- 設定（環境変数から取得） ---
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ロールIDとチャンネルID 
ROLE_IDS = {
    "ADMIN_A": 1426586565035036704,
    "ADMIN_B": 1426586418284859567
}
CHANNEL_IDS = {
    "PUNIPUNI_STONE": 1426577588327022693,
    "BOUNTY_STONE": 1426577819533578391,
    "PUNIPUNI_ACCOUNT": 1426584402347167915,
    "FREE_SALE": 1426574751375036416
}

# インテント設定
intents = discord.Intents.default()
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
supabase: Optional[Client] = None

# 起動時間記録
start_time = datetime.datetime.now()

# -----------------------------------------------------------
# 共通機能
# -----------------------------------------------------------

def get_channel_id_by_type(product_type: str) -> Optional[int]:
    if product_type == "ぷにぷに石垢":
        return CHANNEL_IDS["PUNIPUNI_STONE"]
    elif product_type == "バウンティ石垢":
        return CHANNEL_IDS["BOUNTY_STONE"]
    elif product_type == "ぷにぷに垢":
        return CHANNEL_IDS["PUNIPUNI_ACCOUNT"]
    elif product_type == "自由販売":
        return CHANNEL_IDS["FREE_SALE"]
    return None


def create_embed_message_1(product_data: dict, author: discord.Member) -> discord.Embed:
    embed = discord.Embed(
        title=product_data["product_name"],
        color=discord.Color.green()
    )
    content = (
        f"種類: {product_data['product_type']}\n"
        f"金額: {product_data['price']}円\n"
        f"値下げ交渉: {product_data['negotiation_allowed']}\n"
        f"販売者: {author.mention}"
    )
    embed.description = content
    return embed


def create_embed_message_2(product_data: dict) -> discord.Embed:
    embed = discord.Embed(
        title="購入管理パネル",
        description="販売者はここで管理をしてください。\nもし間違えて完了を押した場合は販売をやり直してください。",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="現在の情報",
        value=(
            f"種類: {product_data['product_type']}\n"
            f"金額: {product_data['price']}円\n"
            f"値下げ交渉: {product_data['negotiation_allowed']}"
        ),
        inline=False
    )
    return embed

# -----------------------------------------------------------
# (UIコンポーネントやView・Modalなど)
# -----------------------------------------------------------
# 🔹ここは元のコードと同じです。販売パネル、管理パネル、編集モーダルなどをそのまま使用できます。
# ※ supabaseへのinsert/updateなどの動作はすべて維持されています。

# -----------------------------------------------------------
# Botイベントとコマンド
# -----------------------------------------------------------

@tasks.loop(seconds=30.0)
async def status_task():
    global start_time
    ping = round(bot.latency * 1000)
    uptime = datetime.datetime.now() - start_time
    total_seconds = int(uptime.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    uptime_msg = f"{hours}時間{minutes}分{seconds}秒"
    activity = discord.Activity(
        name=f"{ping}ms | 稼働{uptime_msg}",
        type=discord.ActivityType.watching
    )
    await bot.change_presence(activity=activity)


@bot.event
async def on_ready():
    print(f"✅ ログイン完了: {bot.user}")
    global supabase, start_time
    start_time = datetime.datetime.now()

    if SUPABASE_URL and SUPABASE_KEY:
        try:
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            print("Supabaseクライアントを初期化しました。")
        except Exception as e:
            print(f"❌ Supabase初期化エラー: {e}")

    try:
        synced = await bot.tree.sync()
        print(f"🌐 スラッシュコマンド {len(synced)} 件同期しました。")
    except Exception as e:
        print(f"❌ コマンド同期エラー: {e}")

    if not status_task.is_running():
        status_task.start()


@bot.tree.command(name="store_open", description="販売パネルを作成します")
@app_commands.default_permissions(manage_channels=True)
async def store_open(interaction: discord.Interaction):
    await interaction.response.send_message("✅ パネルを作成しました。", ephemeral=True)
    embed = discord.Embed(
        title="販売パネル作成",
        description="作成したいパネルの種類を選択してください。",
        color=discord.Color.blue()
    )
    view = SaleSelectView(target_channel=interaction.channel)
    await interaction.channel.send(embed=embed, view=view)


@bot.tree.command(name="store_edit", description="商品の編集をします")
async def store_edit(interaction: discord.Interaction):
    modal = EditUUIDModal()
    await interaction.response.send_modal(modal)

# -----------------------------------------------------------
# Bot実行部 (Railway対応)
# -----------------------------------------------------------
if __name__ == "__main__":
    if not all([DISCORD_BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
        print("❌ 環境変数が足りません。RailwayのVariablesを確認してください。")
    else:
        print("🚀 Discord Botを起動します...")
        bot.run(DISCORD_BOT_TOKEN)
