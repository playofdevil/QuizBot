# QuizBot
Free Quiz Bot

# 🎯 English Quiz Bot

A Telegram Quiz Bot made for Groups. 

## Features
- 3 Categories: Easy, Medium, Hard (100 Q&A each)
- Custom Speed System: `Speed@10s`, `Speed@1m`
- 3x Repeat Logic if no one answers
- Top 10 Leaderboard (`Score@all`)
- Admin Only Speed Change
- Broadcast System for Owner

## Deployment on Render

1. Create a **Web Service** on Render.
2. Connect your GitHub repository.
3. Set the following **Environment Variables**:
   - `BOT_TOKEN`: Your Telegram Bot Token from BotFather.
   - `OWNER_ID`: Your Telegram User ID.
   - `MONGO_URI`: Your MongoDB Connection String.
4. **Build Command:** `pip install -r requirements.txt`
5. **Start Command:** `python main.py`

## Commands
- `Quiz@on` - Start Quiz
- `Quiz@off` - Stop Quiz
- `Speed@10s` - Change Speed (Admins only)
- `Score@me` - Your Score
- `Score@all` - Top 10 Leaderboard
- `Stats@users` / `Stats@groups` - Bot Stats (Owner only)
- `Broadcast@message` - Send message to all (Owner only)
