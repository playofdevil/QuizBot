# 🎯 English Quiz Bot Guide

![English Quiz Bot](welcome.gif)

Yeh bot Telegram groups ke liye ekdum perfect hai. Isme aapko saare commands ki list mil jayegi (Pure English).

## 🛡️ GROUP ADMIN COMMANDS (Admin & Permitted Users)
- `Quiz@on` / `Quiz@off` : Start or stop the live quiz.
- `Speed@5s` (or 10s, 1m) : Set the timer speed for the group.
- `Skip@` : Skip the current question (Bot will reveal the answer).
- `Hint@` : Get a hint for the current question.
- `Pause@q` / `Resume@q` : Pause or resume the ongoing quiz. (Also available as buttons!)
- `GroupStats@` : View total players and points in the group.
- `Yes@on` (Reply/Tag) : Grant quiz control permissions to a user.
- `No@off` (Reply/Tag) : Remove quiz control permissions.
- `SetWelcome@` / `RemoveWelcome@` : Set custom welcome message (Supports variables like `{name}`, `{date}`, `{username}` and media).

## 👤 ALL USERS COMMANDS
- `Score@me` : View your personal score.
- `Score@group` : View the group's top 10 leaderboard.
- `Score@all` : View the global top 10 leaderboard.
- `Profile@username` (or reply) : View any user's profile.
- `Rank@me` : View your global rank.
- `top@fast` : View the Top 10 fastest answer givers globally.
- `help@` : View this commands guide.
- `Report@` : Report a wrong question to the owner.

## 👑 BOT OWNER COMMANDS (Only for the Owner)
- `.on` / `.off` : Globally turn the bot on/off.
- `Top10Details@` : View top 10 users with their ID, Name, and Group.
- `UserInfo@` : (Reply) View detailed info of any user.
- `ForceJoin@channel_link` : Set a compulsory channel to join before playing.
- `RemoveForceJoin@` : Remove the force join channel.
- `BroadcastAll@msg` : Send a message to all users and groups.
- `BroadcastGroups@msg` : Send a message only to groups.
- `BroadcastUsers@msg` : Send a message only to private users.
- `ListGroups@` : View all groups where the bot is added.
- `Stats@users` / `Stats@groups` : View total users and groups.
- `Reset@all` / `Reset@group` : Reset the leaderboard (Deletes points).
- `AddQ@category@Question@Answer` : Add a custom question.
- `DelQ@category@Question` : Delete a custom question.
- `Ban@` / `Unban@` / `Mute@` / `Unmute@` : (Reply/Tag) Control users.

## 🏆 POINT SYSTEM
- **Normal Groups:**
  - Answer within 10 seconds = 100 Points
  - Answer within 20 seconds = 50 Points
  - Answer after 20 seconds = 20 Points
- **VIP Group (Secret):**
  - Answer within 5 seconds = 5240 Points
  - Answer within 10 seconds = 3430 Points
  - Answer after 10 seconds = 1050 Points
