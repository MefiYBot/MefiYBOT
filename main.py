import discord
from discord.ext import commands, tasks
from discord import app_commands, ui, utils
import os
import uuid
from supabase import create_client, Client
from typing import Optional, Dict, Any
from dotenv import load_dotenv 
import datetime

# --- Replit/Uptime Robot対応のためのインポート ---
from flask import Flask
from threading import Thread
# -----------------------------------------------------------

# .envファイルを読み込む
load_dotenv()

# --- 設定（環境変数から取得） ---
DISCORD_BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

# ロールIDとチャンネルID 
ROLE_IDS = {
    "ADMIN_A": 1426586565035036704,  # 特定のロールA (例: 監視ロール)
    "ADMIN_B": 1426586418284859567   # 特定のロールB (例: 管理ロール)
}
CHANNEL_IDS = {
    "PUNIPUNI_STONE": 1426577588327022693, # ぷにぷに石垢
    "BOUNTY_STONE": 1426577819533578391,   # バウンティ石垢
    "PUNIPUNI_ACCOUNT": 1426584402347167915, # ぷにぷに垢
    "FREE_SALE": 1426574751375036416      # 自由販売垢
}

# インテントを設定
intents = discord.Intents.default()
intents.members = True 
intents.message_content = True 

# Botクライアントの作成
bot = commands.Bot(command_prefix='!', intents=intents)
supabase: Optional[Client] = None # 初期化前はNone

# 稼働開始時間を記録するグローバル変数
start_time = datetime.datetime.now() 

# -----------------------------------------------------------
# Keep Alive (Replit/Uptime Robot対応)
# -----------------------------------------------------------

# Flaskアプリのインスタンスを作成
app = Flask(__name__)

@app.route('/')
def home():
    """Uptime Robotからの監視リクエストに応答する"""
    return "Bot is running and alive!"

def run_flask():
    """Webサーバーを起動する関数"""
    # Replitの環境変数PORTがあればそれを使用し、なければ8080を使用
    port = int(os.environ.get("PORT", 8080))
    # host='0.0.0.0'で外部からのアクセスを許可
    print(f"🌐 Flask Webサーバーをポート {port} で起動します...")
    # debug=Falseは本番環境での推奨設定
    app.run(host='0.0.0.0', port=port, debug=False) 

def keep_alive():
    """Webサーバーを別スレッドで起動し、BOTが停止しないようにする"""
    t = Thread(target=run_flask)
    t.daemon = True # メインプロセス終了時にスレッドも終了
    t.start()

# -----------------------------------------------------------
# 共通機能
# -----------------------------------------------------------

def get_channel_id_by_type(product_type: str) -> Optional[int]:
    """種類名に基づいて対応する販売チャンネルIDを返す"""
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
    """埋め込みメッセージ① (販売チャンネル用) を作成"""
    embed = discord.Embed(
        title=product_data["product_name"],
        color=discord.Color.green()
    )
    # product_data['price']がintであることを想定
    content = (
        f"種類: {product_data['product_type']}\n"
        f"金額: {product_data['price']}円\n"
        f"値下げ交渉: {product_data['negotiation_allowed']}\n"
        f"販売者: {author.mention}"
    )
    embed.description = content
    return embed

def create_embed_message_2(product_data: dict) -> discord.Embed:
    """埋め込みメッセージ② (一時チャンネル用) を作成"""
    embed = discord.Embed(
        title="購入管理パネル",
        description="販売者はここで、管理をしてください。\nもし、間違えて完了ボタンを押した場合は販売開始からやり直してください。",
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
# UIコンポーネント (View, Modal)
# -----------------------------------------------------------

class SaleSelect(ui.Select):
    """/store_open コマンドで使用するパネル種類選択メニュー"""
    def __init__(self, target_channel):
        self.target_channel = target_channel
        options = [
            discord.SelectOption(label="石垢", value="石垢"),
            discord.SelectOption(label="ぷにぷに", value="ぷにぷに"),
            discord.SelectOption(label="自由", value="自由"),
        ]
        super().__init__(placeholder="パネルの種類を選択してください...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        selected_type = self.values[0]

        embed = discord.Embed(
            title=f"{selected_type}販売機",
            description=f"{selected_type}の販売を開始する場合は下のボタンを押してください",
            color=discord.Color.green()
        )

        view = SaleStartView(sale_type=selected_type)

        await self.target_channel.send(embed=embed, view=view)
        await interaction.response.send_message(f"✅ 「{selected_type}販売機」パネルを送信しました。", ephemeral=True)


class SaleSelectView(ui.View):
    """/store_open コマンドの埋め込みに付与する View"""
    def __init__(self, target_channel):
        super().__init__(timeout=None)
        self.add_item(SaleSelect(target_channel))

class SaleStartModal(ui.Modal, title="販売メニュー"):
    """販売開始ボタンクリック時に表示するモーダル (新規出品用)"""
    def __init__(self, sale_type: str):
        super().__init__()
        self.sale_type = sale_type

        # ① 商品名
        self.item_name = ui.TextInput(label="商品名", placeholder="商品名を入力してください", max_length=100)
        self.add_item(self.item_name)

        # ② 種類
        if self.sale_type == "石垢":
            self.product_type = ui.TextInput(
                label="種類 (ぷにぷに石垢 or バウンティ石垢)",
                placeholder="ぷにぷに石垢 または バウンティ石垢 を入力",
                max_length=50
            )
            self.add_item(self.product_type)
        elif self.sale_type == "自由":
            self.product_type = ui.TextInput(
                label="種類 (なんでもOK)",
                placeholder="商品の種類を入力",
                max_length=50
            )
            self.add_item(self.product_type)
        # ぷにぷに垢 の場合は質問不要

        # ③ 金額
        self.price = ui.TextInput(label="金額 (半角数字のみ)", placeholder="例: 1000", max_length=10)
        self.add_item(self.price)

        # ④ 値下げ交渉の可否
        self.negotiation = ui.TextInput(
            label="値下げ交渉の可否 (許可 or 拒否)",
            placeholder="許可 または 拒否 を入力",
            max_length=5
        )
        self.add_item(self.negotiation)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True) # 応答を遅延
        global supabase

        # 入力値の取得
        product_name = self.item_name.value
        price_str = self.price.value
        negotiation_str = self.negotiation.value

        # 種類の決定
        if self.sale_type == "ぷにぷに":
            product_type = "ぷにぷに垢"
        else:
            product_type = self.product_type.value

        # --- 検証 ---

        # 金額の検証
        if not price_str.isdigit():
            await interaction.user.send(embed=discord.Embed(
                title="❌ 販売メニューでの入力エラー",
                description="金額の項目は**半角数字のみ**で入力してください。",
                color=discord.Color.red()
            ))
            return await interaction.followup.send("❌ 入力にエラーがあります。DMを確認してください。", ephemeral=True)

        # 値下げ交渉の可否の検証
        if negotiation_str not in ["許可", "拒否"]:
            await interaction.user.send(embed=discord.Embed(
                title="❌ 販売メニューでの入力エラー",
                description="値下げ交渉の可否の項目は**許可**か**拒否**のどちらかのみを入力してください。",
                color=discord.Color.red()
            ))
            return await interaction.followup.send("❌ 入力にエラーがあります。DMを確認してください。", ephemeral=True)

        # 石垢の種類の検証
        if self.sale_type == "石垢" and product_type not in ["ぷにぷに石垢", "バウンティ石垢"]:
            await interaction.user.send(embed=discord.Embed(
                title="❌ 販売メニューでの入力エラー",
                description="石垢販売機では、種類名が**ぷにぷに石垢**または**バウンティ石垢**である必要があります。",
                color=discord.Color.red()
            ))
            return await interaction.followup.send("❌ 入力にエラーがあります。DMを確認してください。", ephemeral=True)

        # --- データの準備と保存 ---
        new_uuid = str(uuid.uuid4())

        product_data = {
            "id": new_uuid,
            "seller_id": str(interaction.user.id),
            "product_name": product_name,
            "product_type": product_type,
            "price": int(price_str),
            "negotiation_allowed": negotiation_str,
            "sale_status": "販売中",
            "negotiator_id": None, 
            "sale_msg_id": None,    
            "temp_channel_id": None, 
            "manage_msg_id": None   
        }

        try:
            # Supabaseにデータ挿入 (テーブル名を 'sales' と仮定)
            supabase.table('sales').insert(product_data).execute()
        except Exception as e:
            print(f"Supabaseへの挿入エラー: {e}")
            await interaction.followup.send("❌ データの記録中にエラーが発生しました。", ephemeral=True)
            return

        # --- 販売チャンネルへの投稿 (埋め込みメッセージ①) ---
        target_channel_id = get_channel_id_by_type(product_type)
        target_channel = bot.get_channel(target_channel_id)

        if not target_channel:
            print(f"チャンネルID {target_channel_id} が見つかりません。")
            await interaction.followup.send("❌ 販売チャンネルが見つかりませんでした。", ephemeral=True)
            return

        embed1 = create_embed_message_1(product_data, interaction.user)
        # 購入・交渉ボタンの View を作成
        view1 = BuyNegotiationView(product_id=new_uuid)

        sale_message = None
        try:
            sale_message = await target_channel.send(embed=embed1, view=view1)

            # メッセージIDをSupabaseに更新
            supabase.table('sales').update({'sale_msg_id': str(sale_message.id)}).eq('id', new_uuid).execute()
        except Exception as e:
            print(f"販売チャンネルへの投稿エラー: {e}")
            await interaction.followup.send("❌ 販売チャンネルへの投稿中にエラーが発生しました。", ephemeral=True)
            return

        # --- 販売者へのDM送信 ---
        dm_embed = discord.Embed(
            title="✅ 販売が正常に開始出来ました。",
            color=discord.Color.blue()
        )
        dm_embed.description = (
            f"商品名: {product_name}\n"
            f"ID (編集時に必要です): `{new_uuid}`"
        )
        await interaction.user.send(embed=dm_embed)

        await interaction.followup.send("✅ 販売が開始されました！詳細はDMをご確認ください。", ephemeral=True)

class ReSaleStartModal(ui.Modal, title="販売メニュー (編集)"):
    """/store_edit で使用する、既存データがプリセットされた編集用モーダル"""
    def __init__(self, sale_data: Dict[str, Any]):
        super().__init__()
        self.sale_data = sale_data
        self.product_id = sale_data['id']
        self.sale_type = sale_data['product_type'] 

        # 編集不可な種類を定義
        UNEDITABLE_TYPES = ["ぷにぷに垢", "ぷにぷに石垢", "バウンティ石垢"]

        # ① 商品名
        self.item_name = ui.TextInput(label="商品名", default=sale_data['product_name'], max_length=100)
        self.add_item(self.item_name)

        # ② 種類 (石垢、ぷにぷに垢、バウンティ石垢の場合は非表示/編集不可)
        if self.sale_type not in UNEDITABLE_TYPES:
            self.product_type = ui.TextInput(
                label="種類",
                default=sale_data['product_type'],
                placeholder="商品の種類を入力",
                max_length=50
            )
            self.add_item(self.product_type)
        else:
            # ぷにぷに垢/石垢 の場合は質問をスキップし、値は元のまま
            self.product_type = None 

        # ③ 金額
        self.price = ui.TextInput(label="金額 (半角数字のみ)", default=str(sale_data['price']), max_length=10)
        self.add_item(self.price)

        # ④ 値下げ交渉の可否
        self.negotiation = ui.TextInput(
            label="値下げ交渉の可否 (許可 or 拒否)",
            default=sale_data['negotiation_allowed'],
            max_length=5
        )
        self.add_item(self.negotiation)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        global supabase

        # 編集不可な種類を定義
        UNEDITABLE_TYPES = ["ぷにぷに垢", "ぷにぷに石垢", "バウンティ石垢"]

        # 入力値の取得
        product_name = self.item_name.value
        price_str = self.price.value
        negotiation_str = self.negotiation.value

        # 種類の決定 (未変更の場合も考慮)
        if self.sale_type in UNEDITABLE_TYPES:
            product_type = self.sale_type # 編集不可な種類は元の値を保持
        else:
            product_type = self.product_type.value # 自由販売は変更可能

        # --- 検証 ---
        if not price_str.isdigit():
            return await interaction.followup.send("❌ 更新エラー: 金額は**半角数字のみ**で入力してください。", ephemeral=True)
        if negotiation_str not in ["許可", "拒否"]:
            return await interaction.followup.send("❌ 更新エラー: 値下げ交渉の可否は**許可**か**拒否**のみを入力してください。", ephemeral=True)
        # 元が石垢/ぷにぷに垢関連の商品の場合、編集後の種類も適切な種類であるか確認
        if self.sale_type in ["ぷにぷに石垢", "バウンティ石垢"] and product_type not in ["ぷにぷに石垢", "バウンティ石垢"]:
            pass

        # --- データの更新 ---
        updated_data = {
            "product_name": product_name,
            "product_type": product_type,
            "price": int(price_str),
            "negotiation_allowed": negotiation_str,
        }

        # Supabaseの更新
        try:
            supabase.table('sales').update(updated_data).eq('id', self.product_id).execute()
        except Exception as e:
            print(f"Supabaseへの更新エラー (UUID: {self.product_id}): {e}")
            return await interaction.followup.send("❌ データの更新中にエラーが発生しました。", ephemeral=True)

        # --- Discordメッセージの更新 ---

        # 1. 販売チャンネルの埋め込みメッセージ①を更新
        try:
            # 種類が変更された可能性を考慮し、新しい種類に基づいてチャンネルIDを取得
            target_channel = bot.get_channel(get_channel_id_by_type(product_type))
            if target_channel and self.sale_data.get('sale_msg_id'):
                sale_message = await target_channel.fetch_message(int(self.sale_data['sale_msg_id']))
                # 更新されたデータで埋め込みを再作成するための準備
                updated_sale_data = self.sale_data.copy()
                updated_sale_data.update(updated_data)

                seller = interaction.guild.get_member(int(self.sale_data['seller_id']))
                new_embed1 = create_embed_message_1(updated_sale_data, seller)

                # 販売状況が「販売中」であればボタンを残す
                new_view1 = BuyNegotiationView(product_id=self.product_id) if updated_sale_data['sale_status'] == "販売中" else None
                await sale_message.edit(embed=new_embed1, view=new_view1)

        except Exception as e:
            print(f"メッセージ①の更新エラー (UUID: {self.product_id}): {e}")

        # 2. 一時チャンネルの埋め込みメッセージ②を更新 (一時チャンネルが存在する場合のみ)
        if self.sale_data.get('temp_channel_id') and self.sale_data.get('manage_msg_id'):
            try:
                temp_channel = bot.get_channel(int(self.sale_data['temp_channel_id']))
                if temp_channel:
                    manage_message = await temp_channel.fetch_message(int(self.sale_data['manage_msg_id']))

                    updated_sale_data['price'] = int(price_str) # priceはModalのvalueからintに変換
                    new_embed2 = create_embed_message_2(updated_sale_data)

                    # PurchaseManagementViewは再利用可能
                    new_view2 = PurchaseManagementView(product_id=self.product_id, seller_id=int(self.sale_data['seller_id']))
                    await manage_message.edit(embed=new_embed2, view=new_view2)
            except Exception as e:
                print(f"メッセージ②の更新エラー (UUID: {self.product_id}): {e}")

        await interaction.followup.send("✅ 正常に更新されました。", ephemeral=True)


class EditUUIDModal(ui.Modal, title="商品の編集 - UUID入力"):
    """/store_edit 実行時に最初に表示するモーダル"""
    def __init__(self):
        super().__init__()

        self.product_uuid = ui.TextInput(
            label="UUID", 
            placeholder="DMにBOTから送信されたIDを入力してください", 
            max_length=36
        )
        self.add_item(self.product_uuid)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        global supabase
        input_uuid = self.product_uuid.value.strip()
        user_id = str(interaction.user.id)

        # 1. UUIDの形式検証 (簡単なチェック)
        try:
            uuid.UUID(input_uuid)
        except ValueError:
            return await interaction.followup.send("❌ UUIDの形式が正しくありません。", ephemeral=True)

        # 2. Supabaseから商品データを取得
        try:
            res = supabase.table('sales').select('*').eq('id', input_uuid).single().execute()
            product_data = res.data
        except Exception:
            # 該当UUIDのデータが存在しない
            return await interaction.followup.send("❌ UUIDが一致してません。", ephemeral=True)

        # 3. ユーザーIDの照合
        if product_data['seller_id'] != user_id:
            # UUIDは存在するが、販売者IDが一致しない
            return await interaction.followup.send("❌ ユーザーIDが一致していません。", ephemeral=True)

        # 4. 全て一致した場合、編集用モーダルを表示させるためのボタンを送信

        class EditButtonView(ui.View):
            def __init__(self, data):
                super().__init__(timeout=60)
                self.data = data

            @ui.button(label="編集モーダルを開く", style=discord.ButtonStyle.blurple)
            async def open_edit_modal(self, interaction: discord.Interaction, button: ui.Button):
                # ユーザーがボタンを押したとき、編集用モーダルを表示
                await interaction.response.send_modal(ReSaleStartModal(sale_data=self.data))

        await interaction.followup.send(
            "✅ 検証成功！下のボタンを押して、編集モーダルを開いてください。", 
            view=EditButtonView(data=product_data), 
            ephemeral=True
        )

class SaleStartView(ui.View):
    """販売パネルの「販売開始」ボタン"""
    def __init__(self, sale_type: str):
        super().__init__(timeout=None)
        self.sale_type = sale_type

    @ui.button(label="販売開始", style=discord.ButtonStyle.green, emoji="🏪")
    async def sale_start_button(self, interaction: discord.Interaction, button: ui.Button):
        # 特定のロール（石垢とぷにぷに垢の販売行為）のチェック
        allowed_roles = [ROLE_IDS["ADMIN_A"], ROLE_IDS["ADMIN_B"]]
        if self.sale_type in ["石垢", "ぷにぷに"]:
            if not any(role.id in allowed_roles for role in interaction.user.roles):
                await interaction.response.send_message(
                    "❌ あなたは石垢またはぷにぷに垢の販売権限を持っていません。", ephemeral=True
                )
                return

        # モーダルを表示
        modal = SaleStartModal(sale_type=self.sale_type)
        await interaction.response.send_modal(modal)

class BuyNegotiationView(ui.View):
    """埋め込みメッセージ①の「購入・交渉をする」ボタン"""
    def __init__(self, product_id: str):
        super().__init__(timeout=None)
        self.product_id = product_id

    @ui.button(label="購入・交渉をする", style=discord.ButtonStyle.green, emoji="💵", custom_id="buy_negotiate_btn")
    async def buy_negotiation_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True) # 応答を遅延
        global supabase

        # 1. Supabaseから商品データを取得
        try:
            res = supabase.table('sales').select('*').eq('id', self.product_id).single().execute()
            product_data = res.data
        except Exception as e:
            print(f"Supabaseからのデータ取得エラー: {e}")
            return await interaction.followup.send("❌ 商品データの取得中にエラーが発生しました。", ephemeral=True)

        # --- 販売者自身による購入・交渉の防止 ---
        seller_id = product_data['seller_id']
        if str(interaction.user.id) == seller_id:
            return await interaction.followup.send(
                "❌ ご自身で出品した商品について、購入・交渉はできません。", ephemeral=True
            )
        # -----------------------------------------------------------

        # 2. 排他制御の確認
        if product_data.get('negotiator_id'):
            return await interaction.followup.send(
                "❌ 現在別のユーザーが交渉しておりますので、交渉できません。", ephemeral=True
            )

        # 3. 交渉中ユーザーとしてDBに記録
        try:
            supabase.table('sales').update({'negotiator_id': str(interaction.user.id)}).eq('id', self.product_id).execute()
        except Exception as e:
            print(f"Supabaseのデータ更新エラー: {e}")
            return await interaction.followup.send("❌ データ更新中にエラーが発生しました。", ephemeral=True)

        # 4. チャンネル権限の設定
        guild = interaction.guild
        seller = guild.get_member(int(product_data['seller_id']))
        negotiator = interaction.user

        # 必要なロール
        admin_role_a = guild.get_role(ROLE_IDS["ADMIN_A"])
        admin_role_b = guild.get_role(ROLE_IDS["ADMIN_B"])

        # チャンネルの権限オーバーライド設定
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            seller: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            negotiator: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            bot.user: discord.PermissionOverwrite(read_messages=True, send_messages=True)
        }
        if admin_role_a:
            overwrites[admin_role_a] = discord.PermissionOverwrite(read_messages=True, send_messages=True)
        if admin_role_b:
            overwrites[admin_role_b] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        # 5. 一時的チャンネルの作成
        channel_name = f"{product_data['product_name']}の購入・交渉"
        try:
            temp_channel = await guild.create_text_channel(
                name=channel_name,
                overwrites=overwrites
            )

            # チャンネルIDをDBに更新
            supabase.table('sales').update({'temp_channel_id': str(temp_channel.id)}).eq('id', self.product_id).execute()
        except Exception as e:
            print(f"チャンネル作成エラー: {e}")
            # エラー時は交渉中ユーザーをリセット
            supabase.table('sales').update({'negotiator_id': None}).eq('id', self.product_id).execute()
            return await interaction.followup.send("❌ 一時チャンネルの作成に失敗しました。", ephemeral=True)

        # 6. メッセージ①と埋め込みメッセージ②の送信

        # メッセージ①
        msg1_content = (
            f"{seller.mention}さん！{product_data['product_name']}に購入・交渉者の{negotiator.mention}様が来られました。\n"
            f"このチャンネルは管理・監視のため、{admin_role_a.mention}{admin_role_b.mention}が居ますが喋り掛けることは特にありません。"
        )
        await temp_channel.send(msg1_content)

        # 埋め込みメッセージ② (管理パネル)
        embed2 = create_embed_message_2(product_data)
        view2 = PurchaseManagementView(product_id=self.product_id, seller_id=seller.id)
        manage_message = await temp_channel.send(embed=embed2, view=view2)

        # 管理メッセージIDをDBに更新
        supabase.table('sales').update({'manage_msg_id': str(manage_message.id)}).eq('id', self.product_id).execute()

        await interaction.followup.send(f"✅ 交渉チャンネル {temp_channel.mention} を作成しました。", ephemeral=True)

class EditPriceModal(ui.Modal, title="金額の編集"):
    """管理パネルの編集ボタンで表示するモーダル"""
    def __init__(self, product_id: str, current_price: str):
        super().__init__()
        self.product_id = product_id

        self.new_price = ui.TextInput(
            label="新しい金額 (半角数字のみ)",
            default=current_price,
            placeholder="新しい金額を入力してください",
            max_length=10
        )
        self.add_item(self.new_price)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True, thinking=True)
        global supabase
        new_price_str = self.new_price.value

        if not new_price_str.isdigit():
            return await interaction.followup.send("❌ 金額は半角数字のみで入力してください。", ephemeral=True)

        new_price = int(new_price_str)

        try:
            # 1. Supabaseから商品データを取得
            res = supabase.table('sales').select('*').eq('id', self.product_id).single().execute()
            product_data = res.data

            # 2. Supabaseの金額を更新
            supabase.table('sales').update({'price': new_price}).eq('id', self.product_id).execute()

            # 3. 埋め込みメッセージの更新に必要な情報を準備
            guild = interaction.guild
            seller = guild.get_member(int(product_data['seller_id']))

            # 4. 販売チャンネルの埋め込みメッセージ①を更新
            target_channel = bot.get_channel(get_channel_id_by_type(product_data['product_type']))
            if target_channel and product_data.get('sale_msg_id'):
                sale_message = await target_channel.fetch_message(int(product_data['sale_msg_id']))
                product_data['price'] = new_price # データ更新
                new_embed1 = create_embed_message_1(product_data, seller)

                new_view1 = BuyNegotiationView(product_id=self.product_id) if product_data['sale_status'] == "販売中" else None
                await sale_message.edit(embed=new_embed1, view=new_view1)

            # 5. 一時的チャンネルの埋め込みメッセージ②を更新
            if product_data.get('manage_msg_id'):
                manage_message = await interaction.channel.fetch_message(int(product_data['manage_msg_id']))
                product_data['price'] = new_price # データ更新
                new_embed2 = create_embed_message_2(product_data)

                new_view2 = PurchaseManagementView(product_id=self.product_id, seller_id=int(product_data['seller_id']))
                await manage_message.edit(embed=new_embed2, view=new_view2)

            await interaction.followup.send("✅ 金額を更新しました。", ephemeral=True)

        except Exception as e:
            print(f"金額更新エラー: {e}")
            await interaction.followup.send("❌ 金額の更新中にエラーが発生しました。", ephemeral=True)


class PurchaseManagementView(ui.View):
    """埋め込みメッセージ②の購入管理パネルのボタン"""
    def __init__(self, product_id: str, seller_id: int):
        super().__init__(timeout=None)
        self.product_id = product_id
        self.seller_id = seller_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """販売者と特定のロールのみ実行可能にする"""
        allowed_roles = [ROLE_IDS["ADMIN_A"], ROLE_IDS["ADMIN_B"]]
        is_seller = interaction.user.id == self.seller_id
        is_admin = any(role.id in allowed_roles for role in interaction.user.roles)

        if not (is_seller or is_admin):
            await interaction.response.send_message("❌ この操作は販売者または管理者のみ実行可能です。", ephemeral=True)
            return False
        return True

    @ui.button(label="購入手続き完了", style=discord.ButtonStyle.green, emoji="✅")
    async def complete_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        global supabase

        try:
            # 1. Supabaseからデータ取得
            res = supabase.table('sales').select('*').eq('id', self.product_id).single().execute()
            product_data = res.data

            # 2. チャンネルから、販売者と購入者を閲覧禁止にする
            guild = interaction.guild
            seller = guild.get_member(int(product_data['seller_id']))
            negotiator = guild.get_member(int(product_data['negotiator_id']))

            if seller:
                await interaction.channel.set_permissions(seller, read_messages=False)
            if negotiator:
                await interaction.channel.set_permissions(negotiator, read_messages=False)

            # 3. 販売チャンネルからその商品の投稿を編集 (購入済み状態)
            target_channel = bot.get_channel(get_channel_id_by_type(product_data['product_type']))
            if target_channel and product_data.get('sale_msg_id'):
                sale_message = await target_channel.fetch_message(int(product_data['sale_msg_id']))

                completed_embed = create_embed_message_1(product_data, seller)
                completed_embed.title = f"【購入済】{completed_embed.title}"
                completed_embed.color = discord.Color.dark_grey()

                # ボタンを削除した View で編集
                await sale_message.edit(embed=completed_embed, view=None)

            # 4. Supabaseのステータスを更新
            supabase.table('sales').update({'sale_status': '購入済み'}).eq('id', self.product_id).execute()

            await interaction.followup.send("✅ 購入手続きが完了しました。チャンネルの閲覧権限を解除しました。", ephemeral=True)

        except Exception as e:
            print(f"購入完了エラー: {e}")
            await interaction.followup.send("❌ 処理中にエラーが発生しました。", ephemeral=True)

    @ui.button(label="購入・交渉不成立", style=discord.ButtonStyle.red, emoji="❌")
    async def cancel_button(self, interaction: discord.Interaction, button: ui.Button):
        await interaction.response.defer(ephemeral=True, thinking=True)
        global supabase

        try:
            # 1. Supabaseからデータ取得
            res = supabase.table('sales').select('*').eq('id', self.product_id).single().execute()
            product_data = res.data

            # 2. チャンネルを削除する
            temp_channel = interaction.channel
            await temp_channel.delete()

            # 3. 販売チャンネルの投稿でボタンを押したらほかのユーザーが購入・交渉できるようにする (DBとメッセージを更新)

            # DBの negotiator_id をリセット
            supabase.table('sales').update({'negotiator_id': None, 'temp_channel_id': None, 'manage_msg_id': None}).eq('id', self.product_id).execute()

            # 販売チャンネルのメッセージを編集
            target_channel = bot.get_channel(get_channel_id_by_type(product_data['product_type']))
            if target_channel and product_data.get('sale_msg_id'):
                sale_message = await target_channel.fetch_message(int(product_data['sale_msg_id']))

                # embedはそのまま、Viewを再作成 (ボタンを押せる状態)
                new_view1 = BuyNegotiationView(product_id=self.product_id)
                await sale_message.edit(embed=sale_message.embeds[0], view=new_view1)

        except discord.errors.NotFound:
             # チャンネルが既に削除されていた場合
            supabase.table('sales').update({'negotiator_id': None, 'temp_channel_id': None, 'manage_msg_id': None}).eq('id', self.product_id).execute()
            pass
        except Exception as e:
            print(f"交渉不成立エラー: {e}")
            await interaction.followup.send("❌ 処理中にエラーが発生しました。", ephemeral=True)

    @ui.button(label="情報を編集", style=discord.ButtonStyle.blurple, emoji="⚙️")
    async def edit_button(self, interaction: discord.Interaction, button: ui.Button):
        global supabase
        # 1. Supabaseから現在の金額を取得
        try:
            res = supabase.table('sales').select('price').eq('id', self.product_id).single().execute()
            current_price = str(res.data['price'])
        except Exception as e:
            print(f"金額取得エラー: {e}")
            return await interaction.response.send_message("❌ 現在の金額を取得できませんでした。", ephemeral=True)

        # 2. モーダルを表示
        modal = EditPriceModal(product_id=self.product_id, current_price=current_price)
        await interaction.response.send_modal(modal)


# -----------------------------------------------------------
# Botイベントとスラッシュコマンド
# -----------------------------------------------------------

@tasks.loop(seconds=30.0)
async def status_task():
    global start_time
    # 1. pingを取得 (ms)
    ping = round(bot.latency * 1000)

    # 2. 稼働時間を計算 (時間のみ)
    uptime_duration = datetime.datetime.now() - start_time
    uptime_hours = int(uptime_duration.total_seconds() // 3600)

    # 3. ステータス文字列を作成
    status_message = f"{ping}ms | 稼働{uptime_hours}時間"

    # 4. ステータスを更新 (ActivityType.watchingを使用)
    activity = discord.Activity(
        name=status_message, 
        type=discord.ActivityType.watching
    )
    await bot.change_presence(activity=activity)


@bot.event
async def on_ready():
    print(f'Logged in as {bot.user} (ID: {bot.user.id})')

    global start_time, supabase
    # 稼働開始時間を再記録
    start_time = datetime.datetime.now() 

    if SUPABASE_URL and SUPABASE_KEY:
        try:
            # 環境変数が揃っていることを確認してからSupabaseクライアントを初期化
            supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
            print("Supabaseクライアントを初期化しました。")
        except Exception as e:
            print(f"❌ Supabaseクライアントの初期化中にエラーが発生しました: {e}")

    # グローバルコマンドの同期
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} command(s)")

        # --- コマンドIDの表示 ---
        print("\n--- Synced Command IDs ---")
        for command in synced:
            print(f"Name: /{command.name}, ID: {command.id}")
        print("--------------------------\n")
        # ---------------------------

    except Exception as e:
        print(f"Failed to sync commands: {e}")

    # ステータス更新タスクの開始 (on_readyでのみ開始)
    if not status_task.is_running():
        status_task.start()

@bot.tree.command(name="store_open", description="販売パネルを作ります")
@app_commands.default_permissions(manage_channels=True) # 管理者権限を推奨
async def store_open_command(interaction: discord.Interaction):
    # 最初にコマンドに返信で「正常に作成できました」を送信
    await interaction.response.send_message("✅ 正常に作成できました", ephemeral=True)

    # その後、返信"なし"で埋め込みメッセージを送信
    embed = discord.Embed(
        title="販売パネル作成",
        description="作成したいパネルの種類を選択してください。",
        color=discord.Color.blue()
    )
    # 選択メニュー付きの View を作成し、実行したチャンネルに送信
    view = SaleSelectView(target_channel=interaction.channel)
    await interaction.channel.send(embed=embed, view=view)

@bot.tree.command(name="store_edit", description="商品の編集をします")
async def store_edit_command(interaction: discord.Interaction):
    """UUIDを入力させるモーダルを表示し、編集プロセスを開始します。"""
    modal = EditUUIDModal()
    await interaction.response.send_modal(modal)


# -----------------------------------------------------------
# Botの実行
# -----------------------------------------------------------
if __name__ == "__main__":
    if not all([DISCORD_BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
        print("❌ エラー: 環境変数 (DISCORD_BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY) がすべて設定されていません。")
        print("`.env`ファイルまたはシステム環境変数を確認してください。")
    elif DISCORD_BOT_TOKEN:
        # 🌐 Webサーバーを別スレッドで起動してBotを生存させる (Replit/Uptime Robot対応)
        print("🌐 Webサーバー (keep_alive) を起動します...")
        keep_alive() 

        # 🤖 Discord Botの実行
        print("🤖 Discord Botを起動します...")
        bot.run(DISCORD_BOT_TOKEN)