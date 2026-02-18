import discord
from discord.ext import commands, tasks
import aiosqlite
import random
import os
import requests
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from collections import defaultdict
import time

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

DB_PATH = 'casino.db'

# Litecoin configuration
HOUSE_LTC_ADDRESS = os.getenv('HOUSE_LTC_ADDRESS')
BLOCKCYPHER_API_KEY = os.getenv('BLOCKCYPHER_API_KEY', '')  # Optional but recommended for higher limits
MIN_DEPOSIT_CONFIRMATIONS = 2
LTC_TO_CHIPS = 10000  # 1 LTC = 10,000 chips

# Security settings
MIN_BET = 10  # Minimum bet amount
MAX_BET = 10000  # Maximum bet amount per game
BET_COOLDOWN = 3  # Seconds between bets per user
DAILY_BONUS_AMOUNT = 100  # Daily free chips
WITHDRAWAL_MIN = 0.001  # Minimum withdrawal in LTC

# Rate limiting
user_last_bet = defaultdict(float)
user_bet_counts = defaultdict(int)
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX_BETS = 20  # max bets per window

# BlockCypher API
def get_address_balance(ltc_address):
    """Get confirmed balance of an LTC address"""
    try:
        url = f"https://api.blockcypher.com/v1/ltc/main/addrs/{ltc_address}/balance"
        if BLOCKCYPHER_API_KEY:
            url += f"?token={BLOCKCYPHER_API_KEY}"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()
        # Convert from satoshis to LTC
        balance_ltc = Decimal(data['balance']) / Decimal(100000000)
        return float(balance_ltc)
    except Exception as e:
        print(f"Error fetching address balance: {e}")
        return 0

def get_address_transactions(ltc_address, limit=50):
    """Get recent transactions for an LTC address"""
    try:
        url = f"https://api.blockcypher.com/v1/ltc/main/addrs/{ltc_address}/full"
        params = {'limit': limit}
        if BLOCKCYPHER_API_KEY:
            params['token'] = BLOCKCYPHER_API_KEY
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get('txs', [])
    except Exception as e:
        print(f"Error fetching transactions: {e}")
        return []

def send_ltc(to_address, amount_ltc, from_address, private_key):
    """Send LTC using BlockCypher (requires private key)"""
    try:
        # Create transaction
        url = "https://api.blockcypher.com/v1/ltc/main/txs/new"
        if BLOCKCYPHER_API_KEY:
            url += f"?token={BLOCKCYPHER_API_KEY}"
        
        # Convert LTC to satoshis
        amount_satoshis = int(Decimal(amount_ltc) * Decimal(100000000))
        
        tx_data = {
            "inputs": [{"addresses": [from_address]}],
            "outputs": [{"addresses": [to_address], "value": amount_satoshis}]
        }
        
        response = requests.post(url, json=tx_data, timeout=10)
        response.raise_for_status()
        tmptx = response.json()
        
        # Note: Full transaction signing requires private key handling
        # For security, you should use a proper wallet library or service
        # This is a simplified example
        return {'success': True, 'tx_hash': tmptx.get('tx', {}).get('hash', 'pending')}
    except Exception as e:
        print(f"Error sending LTC: {e}")
        return {'success': False, 'error': str(e)}

# Initialize database
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # User balances and LTC addresses
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                username TEXT,
                ltc_deposit_address TEXT,
                ltc_withdrawal_address TEXT
            )
        ''')
        
        # House balance
        await db.execute('''
            CREATE TABLE IF NOT EXISTS house (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                balance INTEGER DEFAULT 0,
                ltc_address TEXT
            )
        ''')
        
        # Transaction history
        await db.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                type TEXT,
                ltc_amount REAL,
                ltc_txid TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Deposit monitoring
        await db.execute('''
            CREATE TABLE IF NOT EXISTS pending_deposits (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                ltc_address TEXT,
                ltc_txid TEXT,
                ltc_amount REAL,
                confirmations INTEGER DEFAULT 0,
                processed INTEGER DEFAULT 0,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Daily bonuses tracking
        await db.execute('''
            CREATE TABLE IF NOT EXISTS daily_bonuses (
                user_id INTEGER PRIMARY KEY,
                last_claim DATETIME,
                total_claims INTEGER DEFAULT 0
            )
        ''')
        
        # Game statistics
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                total_bets INTEGER DEFAULT 0,
                total_wagered INTEGER DEFAULT 0,
                total_won INTEGER DEFAULT 0,
                total_lost INTEGER DEFAULT 0,
                biggest_win INTEGER DEFAULT 0,
                games_played INTEGER DEFAULT 0
            )
        ''')
        
        # Initialize house
        if HOUSE_LTC_ADDRESS:
            await db.execute('''
                INSERT OR REPLACE INTO house (id, ltc_address, balance) 
                VALUES (1, ?, 0)
            ''', (HOUSE_LTC_ADDRESS,))
        
        await db.commit()

# Database helper functions
async def get_user_balance(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT balance FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def get_house_balance():
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT balance FROM house WHERE id = 1') as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0

async def update_user_balance(user_id, amount, username):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO users (user_id, balance, username) 
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) 
            DO UPDATE SET balance = balance + ?, username = ?
        ''', (user_id, amount, username, amount, username))
        await db.commit()

async def update_house_balance(amount):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE house SET balance = balance + ? WHERE id = 1', (amount,))
        await db.commit()

async def log_transaction(user_id, amount, trans_type, ltc_amount=0, ltc_txid=''):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO transactions (user_id, amount, type, ltc_amount, ltc_txid) 
            VALUES (?, ?, ?, ?, ?)
        ''', (user_id, amount, trans_type, ltc_amount, ltc_txid))
        await db.commit()

async def get_user_deposit_address(user_id):
    """Get user's unique deposit address (generated)"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT ltc_deposit_address FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0]:
                return row[0]
            
            # For this demo, users will deposit to the house address with a memo/note
            # In production, you'd generate unique addresses using HD wallets
            return HOUSE_LTC_ADDRESS

async def set_user_withdrawal_address(user_id, ltc_address, username):
    """Set user's withdrawal address"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO users (user_id, username, ltc_withdrawal_address) 
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) 
            DO UPDATE SET ltc_withdrawal_address = ?, username = ?
        ''', (user_id, username, ltc_address, ltc_address, username))
        await db.commit()

async def get_user_withdrawal_address(user_id):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT ltc_withdrawal_address FROM users WHERE user_id = ?', (user_id,)) as cursor:
            row = await cursor.fetchone()
            return row[0] if row and row[0] else None

# Security helpers
def check_bet_cooldown(user_id):
    """Check if user is on cooldown"""
    current_time = time.time()
    last_bet_time = user_last_bet.get(user_id, 0)
    if current_time - last_bet_time < BET_COOLDOWN:
        return False, BET_COOLDOWN - (current_time - last_bet_time)
    return True, 0

def check_rate_limit(user_id):
    """Check if user exceeded rate limit"""
    current_time = time.time()
    # Initialize if not exists
    if user_id not in user_bet_counts:
        user_bet_counts[user_id] = []
    
    # Clean old entries
    user_bet_counts[user_id] = [t for t in user_bet_counts[user_id] if current_time - t < RATE_LIMIT_WINDOW]
    
    if len(user_bet_counts[user_id]) >= RATE_LIMIT_MAX_BETS:
        return False
    return True

def update_bet_tracking(user_id):
    """Update bet tracking for cooldown and rate limiting"""
    current_time = time.time()
    user_last_bet[user_id] = current_time
    if user_id not in user_bet_counts:
        user_bet_counts[user_id] = []
    user_bet_counts[user_id].append(current_time)

def validate_bet_amount(bet):
    """Validate bet is within limits"""
    if bet < MIN_BET:
        return False, f"Minimum bet is 🪙 {MIN_BET:,}"
    if bet > MAX_BET:
        return False, f"Maximum bet is 🪙 {MAX_BET:,}"
    return True, ""

# Stats tracking
async def update_user_stats(user_id, wagered, won, lost, game_type):
    """Update user statistics"""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO user_stats (user_id, total_bets, total_wagered, total_won, total_lost, biggest_win, games_played)
            VALUES (?, 1, ?, ?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                total_bets = total_bets + 1,
                total_wagered = total_wagered + ?,
                total_won = total_won + ?,
                total_lost = total_lost + ?,
                biggest_win = MAX(biggest_win, ?),
                games_played = games_played + 1
        ''', (user_id, wagered, won, lost, won, wagered, won, lost, won))
        await db.commit()

async def get_user_stats(user_id):
    """Get user statistics"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT * FROM user_stats WHERE user_id = ?', (user_id,)) as cursor:
            return await cursor.fetchone()

# Deposit monitoring task
@tasks.loop(minutes=5)
async def check_deposits():
    """Check for new deposits to house address"""
    if not HOUSE_LTC_ADDRESS:
        return
    
    try:
        txs = get_address_transactions(HOUSE_LTC_ADDRESS, limit=20)
        
        async with aiosqlite.connect(DB_PATH) as db:
            for tx in txs:
                tx_hash = tx.get('hash')
                confirmations = tx.get('confirmations', 0)
                
                # Check if already processed
                async with db.execute(
                    'SELECT processed FROM pending_deposits WHERE ltc_txid = ?', 
                    (tx_hash,)
                ) as cursor:
                    existing = await cursor.fetchone()
                    if existing and existing[0] == 1:
                        continue
                
                # Look for outputs to house address
                for output in tx.get('outputs', []):
                    if HOUSE_LTC_ADDRESS in output.get('addresses', []):
                        amount_ltc = Decimal(output['value']) / Decimal(100000000)
                        
                        if confirmations >= MIN_DEPOSIT_CONFIRMATIONS:
                            # Process deposit - need to identify user
                            # In production, use unique addresses per user
                            print(f"Detected deposit: {amount_ltc} LTC - TX: {tx_hash}")
                            # Auto-credit would require user identification
    except Exception as e:
        print(f"Error checking deposits: {e}")

# Bot events
@bot.event
async def on_ready():
    await init_db()
    if not HOUSE_LTC_ADDRESS:
        print("WARNING: HOUSE_LTC_ADDRESS not set! Crypto features will be limited.")
    else:
        print(f"House LTC Address: {HOUSE_LTC_ADDRESS}")
        house_ltc = get_address_balance(HOUSE_LTC_ADDRESS)
        print(f"House LTC Balance: {house_ltc} LTC")
        check_deposits.start()
    
    house_chips = await get_house_balance()
    print(f'Bot is ready! Logged in as {bot.user}')
    print(f'House chip balance: {house_chips:,} chips')

# Balance commands
@bot.command(name='balance', aliases=['bal', 'b'])
async def balance(ctx):
    """Check your balance"""
    user_balance = await get_user_balance(ctx.author.id)
    ltc_value = user_balance / LTC_TO_CHIPS
    
    embed = discord.Embed(title="💰 Your Balance", color=discord.Color.gold())
    embed.add_field(name="Chips", value=f"🪙 {user_balance:,}", inline=False)
    embed.add_field(name="LTC Value", value=f"₿ {ltc_value:.4f} LTC", inline=False)
    embed.set_footer(text=f"User: {ctx.author.name} | 1 LTC = {LTC_TO_CHIPS:,} chips")
    await ctx.send(embed=embed)

@bot.command(name='daily', aliases=['bonus'])
async def daily_bonus(ctx):
    """Claim your daily bonus"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT last_claim FROM daily_bonuses WHERE user_id = ?', (ctx.author.id,)) as cursor:
            row = await cursor.fetchone()
            
        now = datetime.now()
        if row:
            last_claim = datetime.fromisoformat(row[0])
            time_diff = now - last_claim
            
            if time_diff < timedelta(hours=24):
                remaining = timedelta(hours=24) - time_diff
                hours = int(remaining.total_seconds() // 3600)
                minutes = int((remaining.total_seconds() % 3600) // 60)
                await ctx.send(f"⏰ Daily bonus already claimed! Come back in {hours}h {minutes}m")
                return
        
        # Give bonus
        await update_user_balance(ctx.author.id, DAILY_BONUS_AMOUNT, ctx.author.name)
        await log_transaction(ctx.author.id, DAILY_BONUS_AMOUNT, 'daily_bonus')
        
        # Update bonus tracking
        await db.execute('''
            INSERT INTO daily_bonuses (user_id, last_claim, total_claims)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                last_claim = ?,
                total_claims = total_claims + 1
        ''', (ctx.author.id, now.isoformat(), now.isoformat()))
        await db.commit()
        
        new_balance = await get_user_balance(ctx.author.id)
        embed = discord.Embed(title="🎁 Daily Bonus Claimed!", color=discord.Color.green())
        embed.add_field(name="Bonus", value=f"🪙 +{DAILY_BONUS_AMOUNT:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.set_footer(text="Come back tomorrow for another bonus!")
        await ctx.send(embed=embed)

@bot.command(name='stats')
async def user_stats(ctx, user: discord.Member = None):
    """View your gambling statistics"""
    target = user or ctx.author
    stats = await get_user_stats(target.id)
    
    if not stats:
        await ctx.send(f"No stats yet for {target.name}!")
        return
    
    _, total_bets, total_wagered, total_won, total_lost, biggest_win, games_played = stats
    net_profit = total_won - total_lost
    win_rate = (total_won / total_wagered * 100) if total_wagered > 0 else 0
    
    embed = discord.Embed(title=f"📊 Stats for {target.name}", color=discord.Color.blue())
    embed.add_field(name="Games Played", value=f"{games_played:,}", inline=True)
    embed.add_field(name="Total Bets", value=f"{total_bets:,}", inline=True)
    embed.add_field(name="Total Wagered", value=f"🪙 {total_wagered:,}", inline=True)
    embed.add_field(name="Total Won", value=f"🪙 {total_won:,}", inline=True)
    embed.add_field(name="Total Lost", value=f"🪙 {total_lost:,}", inline=True)
    embed.add_field(name="Net Profit", value=f"🪙 {net_profit:,}", inline=True)
    embed.add_field(name="Biggest Win", value=f"🪙 {biggest_win:,}", inline=True)
    embed.add_field(name="Win Rate", value=f"{win_rate:.1f}%", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='house')
async def house(ctx):
    """Check house balance"""
    house_balance = await get_house_balance()
    house_ltc_balance = 0
    
    if HOUSE_LTC_ADDRESS:
        house_ltc_balance = get_address_balance(HOUSE_LTC_ADDRESS)
    
    embed = discord.Embed(title="🏛️ House Balance", color=discord.Color.blue())
    embed.add_field(name="Chip Balance", value=f"🪙 {house_balance:,}", inline=False)
    if HOUSE_LTC_ADDRESS:
        embed.add_field(name="LTC Balance", value=f"₿ {house_ltc_balance:.4f} LTC", inline=False)
        embed.add_field(name="Address", value=f"`{HOUSE_LTC_ADDRESS}`", inline=False)
    await ctx.send(embed=embed)

# Crypto deposit/withdrawal
@bot.command(name='deposit')
async def deposit_info(ctx, amount: float = None):
    """Get deposit address and instructions"""
    if not HOUSE_LTC_ADDRESS:
        await ctx.send("❌ Crypto deposits not configured!")
        return
    
    deposit_address = await get_user_deposit_address(ctx.author.id)
    
    embed = discord.Embed(title="💎 Deposit Litecoin", color=discord.Color.green())
    embed.add_field(name="Deposit Address", value=f"`{deposit_address}`", inline=False)
    embed.add_field(
        name="Important Instructions",
        value=(
            f"1. Send LTC to the address above\n"
            f"2. **Include your Discord User ID in memo/note:** `{ctx.author.id}`\n"
            f"3. Wait for {MIN_DEPOSIT_CONFIRMATIONS} confirmations\n"
            f"4. Contact admin to credit your account\n\n"
            f"**Exchange Rate:** 1 LTC = {LTC_TO_CHIPS:,} chips"
        ),
        inline=False
    )
    
    if amount:
        chips = int(amount * LTC_TO_CHIPS)
        embed.add_field(
            name="Deposit Amount",
            value=f"₿ {amount:.4f} LTC → 🪙 {chips:,} chips",
            inline=False
        )
    
    embed.set_footer(text="For security, deposits are manually verified by admins")
    await ctx.send(embed=embed)

@bot.command(name='setwithdraw')
async def set_withdraw(ctx, ltc_address: str):
    """Set your LTC withdrawal address"""
    # Basic validation (LTC addresses start with L or M)
    if not (ltc_address.startswith('L') or ltc_address.startswith('M')) or len(ltc_address) < 26:
        await ctx.send("❌ Invalid Litecoin address! LTC addresses start with 'L' or 'M'")
        return
    
    await set_user_withdrawal_address(ctx.author.id, ltc_address, ctx.author.name)
    
    embed = discord.Embed(title="✅ Withdrawal Address Set", color=discord.Color.green())
    embed.add_field(name="Your LTC Address", value=f"`{ltc_address}`", inline=False)
    embed.add_field(
        name="Next Steps",
        value="Use `!withdraw <amount>` to request a withdrawal",
        inline=False
    )
    await ctx.send(embed=embed)

@bot.command(name='withdraw')
async def withdraw(ctx, amount: float):
    """Request LTC withdrawal"""
    if amount <= 0:
        await ctx.send("❌ Amount must be positive!")
        return
    
    if amount < WITHDRAWAL_MIN:
        await ctx.send(f"❌ Minimum withdrawal is {WITHDRAWAL_MIN} LTC ({int(WITHDRAWAL_MIN * LTC_TO_CHIPS):,} chips)")
        return
    
    # Check withdrawal address
    withdrawal_address = await get_user_withdrawal_address(ctx.author.id)
    if not withdrawal_address:
        await ctx.send("❌ Please set your withdrawal address first using `!setwithdraw <LTC_address>`")
        return
    
    # Convert to chips
    chips_needed = int(amount * LTC_TO_CHIPS)
    user_balance = await get_user_balance(ctx.author.id)
    
    if user_balance < chips_needed:
        await ctx.send(f"❌ Insufficient balance! You have 🪙 {user_balance:,} ({user_balance/LTC_TO_CHIPS:.4f} LTC)")
        return
    
    # Deduct chips
    await update_user_balance(ctx.author.id, -chips_needed, ctx.author.name)
    await log_transaction(ctx.author.id, -chips_needed, 'withdrawal_pending', amount, '')
    
    new_balance = await get_user_balance(ctx.author.id)
    
    embed = discord.Embed(title="⏳ Withdrawal Request Submitted", color=discord.Color.orange())
    embed.add_field(name="Amount", value=f"₿ {amount:.4f} LTC", inline=True)
    embed.add_field(name="Chips Deducted", value=f"🪙 {chips_needed:,}", inline=True)
    embed.add_field(name="Address", value=f"`{withdrawal_address}`", inline=False)
    embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
    embed.set_footer(text="Admin will process your withdrawal within 24 hours")
    
    await ctx.send(embed=embed)
    
    # Notify in console for admin
    print(f"🔔 WITHDRAWAL REQUEST: {ctx.author.name} ({ctx.author.id}) - {amount} LTC to {withdrawal_address}")

# Manual credit command (admin only)
@bot.command(name='credit')
@commands.has_permissions(administrator=True)
async def credit(ctx, user: discord.Member, ltc_amount: float):
    """[ADMIN] Manually credit LTC deposit to user"""
    chips = int(ltc_amount * LTC_TO_CHIPS)
    await update_user_balance(user.id, chips, user.name)
    await update_house_balance(chips)
    await log_transaction(user.id, chips, 'ltc_deposit', ltc_amount, 'manual_credit')
    
    embed = discord.Embed(title="✅ Deposit Credited", color=discord.Color.green())
    embed.add_field(name="User", value=user.mention, inline=True)
    embed.add_field(name="LTC Amount", value=f"₿ {ltc_amount:.4f}", inline=True)
    embed.add_field(name="Chips Credited", value=f"🪙 {chips:,}", inline=True)
    
    await ctx.send(embed=embed)
    
    # DM user
    try:
        await user.send(f"✅ Your deposit of {ltc_amount:.4f} LTC ({chips:,} chips) has been credited!")
    except:
        pass

# Gambling commands with security
@bot.command(name='coinflip', aliases=['cf'])
async def coinflip(ctx, bet: int, choice: str):
    """Flip a coin! Usage: !coinflip <amount> <heads/tails>"""
    # Security checks
    valid, msg = validate_bet_amount(bet)
    if not valid:
        await ctx.send(f"❌ {msg}")
        return
    
    can_bet, cooldown_remaining = check_bet_cooldown(ctx.author.id)
    if not can_bet:
        await ctx.send(f"⏰ Cooldown active! Wait {cooldown_remaining:.1f}s")
        return
    
    if not check_rate_limit(ctx.author.id):
        await ctx.send(f"⚠️ Rate limit exceeded! Slow down.")
        return
    
    choice = choice.lower()
    if choice not in ['heads', 'tails', 'h', 't']:
        await ctx.send("❌ Choose 'heads' or 'tails'!")
        return
    
    user_balance = await get_user_balance(ctx.author.id)
    if user_balance < bet:
        await ctx.send(f"❌ Insufficient balance! You have 🪙 {user_balance:,}")
        return
    
    house_balance = await get_house_balance()
    if house_balance < bet:
        await ctx.send(f"❌ House doesn't have enough chips!")
        return
    
    update_bet_tracking(ctx.author.id)
    
    if choice in ['h', 'heads']:
        choice = 'heads'
    else:
        choice = 'tails'
    
    result = random.choice(['heads', 'tails'])
    won = result == choice
    
    embed = discord.Embed(title="🪙 Coin Flip", color=discord.Color.gold())
    embed.add_field(name="Your Choice", value=choice.capitalize(), inline=True)
    embed.add_field(name="Result", value=f"**{result.capitalize()}**", inline=True)
    embed.add_field(name="Bet", value=f"🪙 {bet:,}", inline=True)
    
    if won:
        winnings = bet
        await update_user_balance(ctx.author.id, winnings, ctx.author.name)
        await update_house_balance(-winnings)
        await log_transaction(ctx.author.id, winnings, 'coinflip_win')
        await update_user_stats(ctx.author.id, bet, winnings, 0, 'coinflip')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"✅ **YOU WIN!**", inline=False)
        embed.add_field(name="Winnings", value=f"🪙 +{winnings:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.green()
    else:
        await update_user_balance(ctx.author.id, -bet, ctx.author.name)
        await update_house_balance(bet)
        await log_transaction(ctx.author.id, -bet, 'coinflip_loss')
        await update_user_stats(ctx.author.id, bet, 0, bet, 'coinflip')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"❌ **YOU LOSE!**", inline=False)
        embed.add_field(name="Lost", value=f"🪙 -{bet:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.red()
    
    await ctx.send(embed=embed)

@bot.command(name='dice', aliases=['roll'])
async def dice(ctx, bet: int):
    """Roll a dice! Win if you roll 4, 5, or 6"""
    if bet <= 0:
        await ctx.send("❌ Bet must be positive!")
        return
    
    user_balance = await get_user_balance(ctx.author.id)
    if user_balance < bet:
        await ctx.send(f"❌ Insufficient balance! You have 🪙 {user_balance:,}")
        return
    
    house_balance = await get_house_balance()
    if house_balance < bet:
        await ctx.send(f"❌ House doesn't have enough chips!")
        return
    
    roll = random.randint(1, 6)
    won = roll >= 4
    
    embed = discord.Embed(title="🎲 Dice Roll", color=discord.Color.gold())
    embed.add_field(name="Your Roll", value=f"**{roll}**", inline=True)
    embed.add_field(name="Win on", value="4, 5, or 6", inline=True)
    embed.add_field(name="Bet", value=f"🪙 {bet:,}", inline=True)
    
    if won:
        winnings = bet
        await update_user_balance(ctx.author.id, winnings, ctx.author.name)
        await update_house_balance(-winnings)
        await log_transaction(ctx.author.id, winnings, 'dice_win')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"✅ **YOU WIN!**", inline=False)
        embed.add_field(name="Winnings", value=f"🪙 +{winnings:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.green()
    else:
        await update_user_balance(ctx.author.id, -bet, ctx.author.name)
        await update_house_balance(bet)
        await log_transaction(ctx.author.id, -bet, 'dice_loss')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"❌ **YOU LOSE!**", inline=False)
        embed.add_field(name="Lost", value=f"🪙 -{bet:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.red()
    
    await ctx.send(embed=embed)

@bot.command(name='slots')
async def slots(ctx, bet: int):
    """Play slots! Three matching symbols = 5x win"""
    if bet <= 0:
        await ctx.send("❌ Bet must be positive!")
        return
    
    user_balance = await get_user_balance(ctx.author.id)
    if user_balance < bet:
        await ctx.send(f"❌ Insufficient balance! You have 🪙 {user_balance:,}")
        return
    
    house_balance = await get_house_balance()
    max_payout = bet * 5
    if house_balance < max_payout:
        await ctx.send(f"❌ House doesn't have enough chips for max payout!")
        return
    
    symbols = ['🍒', '🍋', '🍊', '🍇', '💎', '7️⃣']
    reel1 = random.choice(symbols)
    reel2 = random.choice(symbols)
    reel3 = random.choice(symbols)
    
    embed = discord.Embed(title="🎰 Slot Machine", color=discord.Color.gold())
    embed.add_field(name="Result", value=f"**{reel1} | {reel2} | {reel3}**", inline=False)
    embed.add_field(name="Bet", value=f"🪙 {bet:,}", inline=True)
    
    if reel1 == reel2 == reel3:
        winnings = bet * 5
        await update_user_balance(ctx.author.id, winnings, ctx.author.name)
        await update_house_balance(-winnings)
        await log_transaction(ctx.author.id, winnings, 'slots_win')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"🎉 **JACKPOT! 5X WIN!**", inline=False)
        embed.add_field(name="Winnings", value=f"🪙 +{winnings:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.gold()
    else:
        await update_user_balance(ctx.author.id, -bet, ctx.author.name)
        await update_house_balance(bet)
        await log_transaction(ctx.author.id, -bet, 'slots_loss')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"❌ **NO MATCH**", inline=False)
        embed.add_field(name="Lost", value=f"🪙 -{bet:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.red()
    
    await ctx.send(embed=embed)

@bot.command(name='blackjack', aliases=['bj'])
async def blackjack(ctx, bet: int):
    """Play Blackjack! Get closer to 21 than dealer. Usage: !blackjack <amount>"""
    # Security checks
    valid, msg = validate_bet_amount(bet)
    if not valid:
        await ctx.send(f"❌ {msg}")
        return
    
    can_bet, cooldown_remaining = check_bet_cooldown(ctx.author.id)
    if not can_bet:
        await ctx.send(f"⏰ Cooldown active! Wait {cooldown_remaining:.1f}s")
        return
    
    if not check_rate_limit(ctx.author.id):
        await ctx.send(f"⚠️ Rate limit exceeded! Slow down.")
        return
    
    user_balance = await get_user_balance(ctx.author.id)
    if user_balance < bet:
        await ctx.send(f"❌ Insufficient balance! You have 🪙 {user_balance:,}")
        return
    
    house_balance = await get_house_balance()
    max_payout = bet * 2
    if house_balance < max_payout:
        await ctx.send(f"❌ House doesn't have enough chips for max payout!")
        return
    
    update_bet_tracking(ctx.author.id)
    
    # Create deck and deal
    deck = ['A','2','3','4','5','6','7','8','9','10','J','Q','K'] * 4
    random.shuffle(deck)
    
    player_hand = [deck.pop(), deck.pop()]
    dealer_hand = [deck.pop(), deck.pop()]
    
    def card_value(hand):
        """Calculate hand value"""
        value = 0
        aces = 0
        for card in hand:
            if card in ['J','Q','K']:
                value += 10
            elif card == 'A':
                aces += 1
                value += 11
            else:
                value += int(card)
        
        while value > 21 and aces:
            value -= 10
            aces -= 1
        return value
    
    player_value = card_value(player_hand)
    dealer_value = card_value(dealer_hand)
    
    # Check for natural blackjack
    if player_value == 21:
        if dealer_value == 21:
            # Push
            embed = discord.Embed(title="🃏 Blackjack - Push!", color=discord.Color.gold())
            embed.add_field(name="Your Hand", value=f"{' '.join(player_hand)} = 21", inline=False)
            embed.add_field(name="Dealer Hand", value=f"{' '.join(dealer_hand)} = 21", inline=False)
            embed.add_field(name="Result", value="Both Blackjack! Bet returned.", inline=False)
            await ctx.send(embed=embed)
            return
        else:
            # Player blackjack wins 2.5x
            winnings = int(bet * 1.5)
            await update_user_balance(ctx.author.id, winnings, ctx.author.name)
            await update_house_balance(-winnings)
            await log_transaction(ctx.author.id, winnings, 'blackjack_win')
            await update_user_stats(ctx.author.id, bet, winnings, 0, 'blackjack')
            
            new_balance = await get_user_balance(ctx.author.id)
            embed = discord.Embed(title="🃏 Blackjack - Natural 21!", color=discord.Color.green())
            embed.add_field(name="Your Hand", value=f"{' '.join(player_hand)} = 21 🎉", inline=False)
            embed.add_field(name="Dealer Hand", value=f"{' '.join(dealer_hand)} = {dealer_value}", inline=False)
            embed.add_field(name="Winnings", value=f"🪙 +{winnings:,} (1.5x)", inline=True)
            embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
            await ctx.send(embed=embed)
            return
    
    # Dealer plays (hits until 17+)
    while dealer_value < 17:
        dealer_hand.append(deck.pop())
        dealer_value = card_value(dealer_hand)
    
    # Determine winner
    embed = discord.Embed(title="🃏 Blackjack", color=discord.Color.gold())
    embed.add_field(name="Your Hand", value=f"{' '.join(player_hand)} = {player_value}", inline=False)
    embed.add_field(name="Dealer Hand", value=f"{' '.join(dealer_hand)} = {dealer_value}", inline=False)
    
    if player_value > 21:
        # Player busts
        await update_user_balance(ctx.author.id, -bet, ctx.author.name)
        await update_house_balance(bet)
        await log_transaction(ctx.author.id, -bet, 'blackjack_loss')
        await update_user_stats(ctx.author.id, bet, 0, bet, 'blackjack')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value="❌ **BUST! YOU LOSE!**", inline=False)
        embed.add_field(name="Lost", value=f"🪙 -{bet:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.red()
    elif dealer_value > 21 or player_value > dealer_value:
        # Player wins
        winnings = bet
        await update_user_balance(ctx.author.id, winnings, ctx.author.name)
        await update_house_balance(-winnings)
        await log_transaction(ctx.author.id, winnings, 'blackjack_win')
        await update_user_stats(ctx.author.id, bet, winnings, 0, 'blackjack')
        
        new_balance = await get_user_balance(ctx.author.id)
        result_msg = "Dealer Bust!" if dealer_value > 21 else "You Win!"
        embed.add_field(name="Result", value=f"✅ **{result_msg}**", inline=False)
        embed.add_field(name="Winnings", value=f"🪙 +{winnings:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.green()
    elif player_value == dealer_value:
        # Push
        embed.add_field(name="Result", value="🤝 **PUSH! Bet Returned**", inline=False)
        embed.color = discord.Color.gold()
    else:
        # Dealer wins
        await update_user_balance(ctx.author.id, -bet, ctx.author.name)
        await update_house_balance(bet)
        await log_transaction(ctx.author.id, -bet, 'blackjack_loss')
        await update_user_stats(ctx.author.id, bet, 0, bet, 'blackjack')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value="❌ **DEALER WINS!**", inline=False)
        embed.add_field(name="Lost", value=f"🪙 -{bet:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.red()
    
    await ctx.send(embed=embed)

@bot.command(name='leaderboard', aliases=['lb', 'top'])
async def leaderboard(ctx):
    """Show top players"""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute('SELECT username, balance FROM users ORDER BY balance DESC LIMIT 10') as cursor:
            rows = await cursor.fetchall()
    
    if not rows:
        await ctx.send("No players yet!")
        return
    
    embed = discord.Embed(title="🏆 Leaderboard - Top Players", color=discord.Color.gold())
    
    medals = ['🥇', '🥈', '🥉']
    for i, (username, balance) in enumerate(rows, 1):
        medal = medals[i-1] if i <= 3 else f"#{i}"
        ltc_value = balance / LTC_TO_CHIPS
        embed.add_field(
            name=f"{medal} {username}", 
            value=f"🪙 {balance:,} (₿ {ltc_value:.4f} LTC)", 
            inline=False
        )
    
    await ctx.send(embed=embed)

@bot.command(name='casino')
async def casino_help(ctx):
    """Show all casino commands"""
    embed = discord.Embed(title="🎰 Crypto Casino Bot", color=discord.Color.blue())
    
    embed.add_field(
        name="💎 Crypto Commands",
        value=(
            "`!deposit` - Get LTC deposit address\n"
            "`!setwithdraw <address>` - Set withdrawal address\n"
            "`!withdraw <LTC>` - Request LTC withdrawal\n"
            "`!balance` - Check balance\n"
            "`!house` - Check house balance"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎁 Bonuses",
        value=(
            "`!daily` - Claim daily bonus (100 chips)\n"
            "`!stats [@user]` - View gambling statistics"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎲 Games",
        value=(
            "`!coinflip <bet> <h/t>` - Flip a coin (2x)\n"
            "`!dice <bet>` - Roll dice, win on 4-6 (2x)\n"
            "`!slots <bet>` - Spin slots, match 3 (5x)\n"
            "`!blackjack <bet>` - Play blackjack (2x/1.5x)"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📊 Leaderboard",
        value="`!leaderboard` - Top players",
        inline=False
    )
    
    embed.add_field(
        name="🔒 Security",
        value=(
            f"Min bet: 🪙 {MIN_BET:,} | Max bet: 🪙 {MAX_BET:,}\n"
            f"Cooldown: {BET_COOLDOWN}s between bets\n"
            f"Rate limit: {RATE_LIMIT_MAX_BETS} bets per {RATE_LIMIT_WINDOW}s"
        ),
        inline=False
    )
    
    embed.set_footer(text=f"1 LTC = {LTC_TO_CHIPS:,} chips")
    
    await ctx.send(embed=embed)

# Run bot
if __name__ == '__main__':
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not found!")
        exit(1)
    
    if not HOUSE_LTC_ADDRESS:
        print("WARNING: HOUSE_LTC_ADDRESS not set! Set it for crypto features.")
    
    bot.run(TOKEN)
