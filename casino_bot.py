import discord
from discord.ext import commands
import aiosqlite
import random
import os
from datetime import datetime

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

DB_PATH = 'casino.db'

# Initialize database
async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        # User balances table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                balance INTEGER DEFAULT 0,
                username TEXT
            )
        ''')
        
        # House balance table
        await db.execute('''
            CREATE TABLE IF NOT EXISTS house (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                balance INTEGER DEFAULT 10000
            )
        ''')
        
        # Transaction history
        await db.execute('''
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                amount INTEGER,
                type TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Initialize house balance if not exists
        await db.execute('INSERT OR IGNORE INTO house (id, balance) VALUES (1, 10000)')
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

async def log_transaction(user_id, amount, trans_type):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            INSERT INTO transactions (user_id, amount, type) 
            VALUES (?, ?, ?)
        ''', (user_id, amount, trans_type))
        await db.commit()

# Bot events
@bot.event
async def on_ready():
    await init_db()
    print(f'Bot is ready! Logged in as {bot.user}')
    print(f'House balance: {await get_house_balance()} chips')

# Balance commands
@bot.command(name='balance', aliases=['bal', 'b'])
async def balance(ctx):
    """Check your balance"""
    user_balance = await get_user_balance(ctx.author.id)
    embed = discord.Embed(title="💰 Your Balance", color=discord.Color.gold())
    embed.add_field(name="Chips", value=f"🪙 {user_balance:,}", inline=False)
    embed.set_footer(text=f"User: {ctx.author.name}")
    await ctx.send(embed=embed)

@bot.command(name='house')
async def house(ctx):
    """Check house balance"""
    house_balance = await get_house_balance()
    embed = discord.Embed(title="🏛️ House Balance", color=discord.Color.blue())
    embed.add_field(name="Total Chips", value=f"🪙 {house_balance:,}", inline=False)
    await ctx.send(embed=embed)

# Deposit/Withdrawal commands
@bot.command(name='deposit', aliases=['dep'])
async def deposit(ctx, amount: int):
    """Deposit chips into your account"""
    if amount <= 0:
        await ctx.send("❌ Amount must be positive!")
        return
    
    await update_user_balance(ctx.author.id, amount, ctx.author.name)
    await log_transaction(ctx.author.id, amount, 'deposit')
    
    new_balance = await get_user_balance(ctx.author.id)
    embed = discord.Embed(title="✅ Deposit Successful", color=discord.Color.green())
    embed.add_field(name="Deposited", value=f"🪙 {amount:,}", inline=True)
    embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='withdraw', aliases=['wd'])
async def withdraw(ctx, amount: int):
    """Withdraw chips from your account"""
    if amount <= 0:
        await ctx.send("❌ Amount must be positive!")
        return
    
    user_balance = await get_user_balance(ctx.author.id)
    if user_balance < amount:
        await ctx.send(f"❌ Insufficient balance! You have 🪙 {user_balance:,}")
        return
    
    await update_user_balance(ctx.author.id, -amount, ctx.author.name)
    await log_transaction(ctx.author.id, -amount, 'withdrawal')
    
    new_balance = await get_user_balance(ctx.author.id)
    embed = discord.Embed(title="✅ Withdrawal Successful", color=discord.Color.green())
    embed.add_field(name="Withdrawn", value=f"🪙 {amount:,}", inline=True)
    embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
    await ctx.send(embed=embed)

# Gambling commands
@bot.command(name='coinflip', aliases=['cf'])
async def coinflip(ctx, bet: int, choice: str):
    """Flip a coin! Usage: !coinflip <amount> <heads/tails>"""
    choice = choice.lower()
    if choice not in ['heads', 'tails', 'h', 't']:
        await ctx.send("❌ Choose 'heads' or 'tails'!")
        return
    
    if bet <= 0:
        await ctx.send("❌ Bet must be positive!")
        return
    
    user_balance = await get_user_balance(ctx.author.id)
    if user_balance < bet:
        await ctx.send(f"❌ Insufficient balance! You have 🪙 {user_balance:,}")
        return
    
    house_balance = await get_house_balance()
    if house_balance < bet:
        await ctx.send(f"❌ House doesn't have enough chips! House balance: 🪙 {house_balance:,}")
        return
    
    # Normalize choice
    if choice in ['h', 'heads']:
        choice = 'heads'
    else:
        choice = 'tails'
    
    # Flip coin
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
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"✅ **YOU WIN!**", inline=False)
        embed.add_field(name="Winnings", value=f"🪙 +{winnings:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.green()
    else:
        await update_user_balance(ctx.author.id, -bet, ctx.author.name)
        await update_house_balance(bet)
        await log_transaction(ctx.author.id, -bet, 'coinflip_loss')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"❌ **YOU LOSE!**", inline=False)
        embed.add_field(name="Lost", value=f"🪙 -{bet:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.red()
    
    await ctx.send(embed=embed)

@bot.command(name='dice', aliases=['roll'])
async def dice(ctx, bet: int):
    """Roll a dice! Win if you roll 4, 5, or 6. Usage: !dice <amount>"""
    if bet <= 0:
        await ctx.send("❌ Bet must be positive!")
        return
    
    user_balance = await get_user_balance(ctx.author.id)
    if user_balance < bet:
        await ctx.send(f"❌ Insufficient balance! You have 🪙 {user_balance:,}")
        return
    
    house_balance = await get_house_balance()
    if house_balance < bet:
        await ctx.send(f"❌ House doesn't have enough chips! House balance: 🪙 {house_balance:,}")
        return
    
    # Roll dice
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
    """Play slots! Three matching symbols = 5x win. Usage: !slots <amount>"""
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
        await ctx.send(f"❌ House doesn't have enough chips for max payout! House balance: 🪙 {house_balance:,}")
        return
    
    # Slot symbols
    symbols = ['🍒', '🍋', '🍊', '🍇', '💎', '7️⃣']
    
    # Spin slots
    reel1 = random.choice(symbols)
    reel2 = random.choice(symbols)
    reel3 = random.choice(symbols)
    
    embed = discord.Embed(title="🎰 Slot Machine", color=discord.Color.gold())
    embed.add_field(name="Result", value=f"**{reel1} | {reel2} | {reel3}**", inline=False)
    embed.add_field(name="Bet", value=f"🪙 {bet:,}", inline=True)
    
    # Check win
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

@bot.command(name='roulette', aliases=['roul'])
async def roulette(ctx, bet: int, choice: str):
    """Play roulette! Bet on red, black, or a number (0-36). Usage: !roulette <amount> <red/black/number>"""
    if bet <= 0:
        await ctx.send("❌ Bet must be positive!")
        return
    
    user_balance = await get_user_balance(ctx.author.id)
    if user_balance < bet:
        await ctx.send(f"❌ Insufficient balance! You have 🪙 {user_balance:,}")
        return
    
    choice = choice.lower()
    
    # Determine bet type and payout multiplier
    if choice in ['red', 'black']:
        bet_type = 'color'
        multiplier = 2
    elif choice.isdigit() and 0 <= int(choice) <= 36:
        bet_type = 'number'
        multiplier = 35
    else:
        await ctx.send("❌ Invalid choice! Use 'red', 'black', or a number (0-36)")
        return
    
    house_balance = await get_house_balance()
    max_payout = bet * multiplier
    if house_balance < max_payout:
        await ctx.send(f"❌ House doesn't have enough chips for max payout! House balance: 🪙 {house_balance:,}")
        return
    
    # Spin roulette
    number = random.randint(0, 36)
    red_numbers = [1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36]
    color = 'red' if number in red_numbers else 'black' if number != 0 else 'green'
    
    embed = discord.Embed(title="🎡 Roulette", color=discord.Color.gold())
    embed.add_field(name="Your Bet", value=choice.capitalize(), inline=True)
    embed.add_field(name="Result", value=f"**{number} ({color.upper()})**", inline=True)
    embed.add_field(name="Bet Amount", value=f"🪙 {bet:,}", inline=True)
    
    # Check win
    won = False
    if bet_type == 'color' and color == choice:
        won = True
    elif bet_type == 'number' and int(choice) == number:
        won = True
    
    if won:
        winnings = bet * multiplier
        await update_user_balance(ctx.author.id, winnings, ctx.author.name)
        await update_house_balance(-winnings)
        await log_transaction(ctx.author.id, winnings, 'roulette_win')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"✅ **YOU WIN!**", inline=False)
        embed.add_field(name="Winnings", value=f"🪙 +{winnings:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.green()
    else:
        await update_user_balance(ctx.author.id, -bet, ctx.author.name)
        await update_house_balance(bet)
        await log_transaction(ctx.author.id, -bet, 'roulette_loss')
        
        new_balance = await get_user_balance(ctx.author.id)
        embed.add_field(name="Result", value=f"❌ **YOU LOSE!**", inline=False)
        embed.add_field(name="Lost", value=f"🪙 -{bet:,}", inline=True)
        embed.add_field(name="New Balance", value=f"🪙 {new_balance:,}", inline=True)
        embed.color = discord.Color.red()
    
    await ctx.send(embed=embed)

# Leaderboard
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
        embed.add_field(
            name=f"{medal} {username}", 
            value=f"🪙 {balance:,}", 
            inline=False
        )
    
    await ctx.send(embed=embed)

# Help command
@bot.command(name='casino')
async def casino_help(ctx):
    """Show all casino commands"""
    embed = discord.Embed(title="🎰 Casino Bot Commands", color=discord.Color.blue())
    
    embed.add_field(
        name="💰 Balance Commands",
        value=(
            "`!balance` / `!bal` - Check your balance\n"
            "`!deposit <amount>` - Deposit chips\n"
            "`!withdraw <amount>` - Withdraw chips\n"
            "`!house` - Check house balance"
        ),
        inline=False
    )
    
    embed.add_field(
        name="🎲 Games",
        value=(
            "`!coinflip <bet> <h/t>` - Flip a coin (2x)\n"
            "`!dice <bet>` - Roll dice, win on 4-6 (2x)\n"
            "`!slots <bet>` - Spin slots, match 3 (5x)\n"
            "`!roulette <bet> <red/black/0-36>` - Play roulette (2x/35x)"
        ),
        inline=False
    )
    
    embed.add_field(
        name="📊 Stats",
        value="`!leaderboard` / `!lb` - Top players",
        inline=False
    )
    
    await ctx.send(embed=embed)

# Run bot
if __name__ == '__main__':
    TOKEN = os.getenv('DISCORD_BOT_TOKEN')
    if not TOKEN:
        print("ERROR: DISCORD_BOT_TOKEN not found in environment variables!")
        print("Please set your Discord bot token:")
        print("export DISCORD_BOT_TOKEN='your_token_here'")
        exit(1)
    
    bot.run(TOKEN)
