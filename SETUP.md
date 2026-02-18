# Discord Casino Bot Setup Guide

## Features
✅ **House Balance System** - Bot manages its own bankroll  
✅ **Automatic Deposits** - Users can add chips instantly  
✅ **Automatic Withdrawals** - Users can cash out anytime  
✅ **Multiple Games** - Coinflip, Dice, Slots, Roulette  
✅ **Transaction Logging** - All deposits/withdrawals/bets tracked  
✅ **Leaderboard** - Top players ranking  
✅ **SQLite Database** - Persistent storage  

## Setup Instructions

### 1. Create a Discord Bot

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click "New Application" and give it a name
3. Go to "Bot" section and click "Add Bot"
4. Under "Privileged Gateway Intents", enable:
   - ✅ Message Content Intent
5. Click "Reset Token" and copy your bot token
6. Go to "OAuth2" > "URL Generator"
7. Select scopes: `bot`
8. Select bot permissions: `Send Messages`, `Read Messages/View Channels`, `Embed Links`
9. Copy the generated URL and open it in browser to invite bot to your server

### 2. Set Bot Token

You need to add your Discord bot token as a secret:

```bash
export DISCORD_BOT_TOKEN='your_token_here'
```

Or request it to be stored securely (recommended).

### 3. Run the Bot

```bash
# Make sure you're in the project directory
cd /data/users/6VNzSUKI6Pb9RxKkcPCqcqbdwJo2/Workspace/discord_casino_house_bot

# Run the bot
uv run casino_bot.py
```

## Commands

### Balance Commands
- `!balance` or `!bal` - Check your balance
- `!deposit <amount>` - Deposit chips (e.g., `!deposit 1000`)
- `!withdraw <amount>` - Withdraw chips (e.g., `!withdraw 500`)
- `!house` - Check house balance

### Games
- `!coinflip <bet> <heads/tails>` - Flip a coin (2x payout)
  - Example: `!coinflip 100 heads`
  
- `!dice <bet>` - Roll dice, win on 4-6 (2x payout)
  - Example: `!dice 50`
  
- `!slots <bet>` - Spin slots, match 3 symbols (5x payout)
  - Example: `!slots 100`
  
- `!roulette <bet> <red/black/number>` - Play roulette
  - Color bet: 2x payout - Example: `!roulette 100 red`
  - Number bet: 35x payout - Example: `!roulette 10 17`

### Stats
- `!leaderboard` or `!lb` - View top players
- `!casino` - Show all commands

## Game Mechanics

### House Balance
- The house starts with 10,000 chips
- When players lose, chips go to the house
- When players win, chips come from the house
- House balance must be sufficient for maximum possible payout

### Payouts
- **Coinflip**: 2x (50% chance)
- **Dice**: 2x (50% chance - win on 4, 5, or 6)
- **Slots**: 5x (match all 3 symbols)
- **Roulette Color**: 2x (red/black)
- **Roulette Number**: 35x (specific number 0-36)

### Transaction System
- All deposits, withdrawals, wins, and losses are logged
- Balances are tracked per Discord user ID
- Database persists across bot restarts

## Database Structure

The bot creates `casino.db` with three tables:

1. **users** - User balances and usernames
2. **house** - House bankroll (single row)
3. **transactions** - Full transaction history

## Troubleshooting

**Bot doesn't respond:**
- Check bot has proper permissions in Discord server
- Verify "Message Content Intent" is enabled
- Check bot token is correct

**"Insufficient balance" errors:**
- Users must deposit chips first: `!deposit 1000`
- Check user balance with: `!balance`

**"House doesn't have enough chips":**
- House ran out of money (players are winning!)
- You can manually add to house balance in the database
- Or adjust initial house balance in code (line 31)

## Security Notes

- Bot token should be kept secret
- Do not share your token publicly
- Use environment variables or secure storage
- This is a fun/demo bot - not for real money gambling
