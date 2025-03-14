import discord
from discord.ext import commands, tasks
from datetime import datetime, timedelta
import pytz
import json
import requests
from fuzzywuzzy import process
from enum import Enum
import asyncio
import asyncpg
import re
from discord.errors import Forbidden
from functools import lru_cache
import os

@lru_cache(maxsize=100)
def cached_find_closest_race(input_text):
    return find_closest_race(input_text, f1_calendar)

BOT_OWNER_ID = 88645942089117696  # Replace with your Discord ID
CENTRAL_REPORT_WEBHOOK = "https://discord.com/api/webhooks/1346235701511065623/RRj55svzF1WUYiwqWRSmx6BHB-A0tioXqRzqfDL8lx7l_FdyYzXRG5F_Y9E5sLDr1ep3"

# API keys
OPENWEATHERMAP_API_KEY = "c4a1b7e5d391c0fc44e92cb00b4899d0"
WEATHERAPI_API_KEY = "95088d11eeae4f5c80c40858250203"
OPENCAGE_API_KEY = "acdc9dd04c0845f19083f2761f0a8e6a"

# Donation links
DONATION_LINKS = {
    "Ko-fi": "https://ko-fi.com/gridguru",
    "Buy Me a Coffee": "https://www.buymeacoffee.com/gridguru"
}

# Supabase configuration (replace with your credentials)
SUPABASE_URL = os.getenv("SUPABASE_URL", "your-supabase-url")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "your-supabase-key")
DATABASE_URL = f"postgresql://postgres:Marsupial_2709@db.owtqjpejwdjzskblbbqt.supabase.co:5432/postgres"

# Load F1 calendar
def load_f1_calendar():
    with open("f1_2025_calendar.json", "r", encoding="utf-8") as file:
        return json.load(file)["races"]

f1_calendar = load_f1_calendar()

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None
)

# Timezone mapping
timezone_mapping = {
    "GMT": "Europe/London", "EST": "America/New_York", "PST": "America/Los_Angeles",
    "CST": "America/Chicago", "IST": "Asia/Kolkata", "AEST": "Australia/Sydney",
    "CET": "Europe/Berlin", "AEDT": "Australia/Sydney", "PDT": "America/Los_Angeles",
    "EDT": "America/New_York", "BST": "Europe/London"
}

# Valid teams and drivers
VALID_TEAMS = ["Red Bull", "Mercedes", "Ferrari", "McLaren", "Aston Martin", "Alpine", "Williams", "Racing Bulls", "Sauber", "Haas"]
VALID_DRIVERS = ["Verstappen", "Lawson", "Hamilton", "Russell", "Leclerc", "Sainz", "Norris", "Piastri", "Alonso", "Stroll", "Ocon", "Gasly", "Antonelli", "Hadjar", "Tsunoda", "Bearman", "Doohan", "Hulkenberg", "Albon", "Bortoleto"]
VALID_DRIVERS_LOWER = [d.lower() for d in VALID_DRIVERS]
VALID_TEAMS_LOWER = [t.lower() for t in VALID_TEAMS]

# Prediction categories
class RaceCategory(Enum):
    RACE_WINNER = "race_winner"
    POLE_SITTER = "pole_sitter"
    PODIUM = "podium"
    FIRST_CRASH = "first_crash"

CATEGORY_FUZZY_MAP = {
    "race_winner": ["race winner", "winner", "race win", "who wins"],
    "pole_sitter": ["pole sitter", "pole", "qualifying winner", "fastest qualifier"],
    "podium": ["podium", "2nd and 3rd", "podium finishers", "top three"],
    "first_crash": ["first crash", "first dnf", "first retirement", "first out"]
}

CATEGORY_DISPLAY_NAMES = {
    "race_winner": "Race Winner",
    "pole_sitter": "Pole Sitter",
    "podium": "Podium Finishers (2nd & 3rd)",
    "first_crash": "First Crash",
    "drivers_champion": "Drivers' Champion",
    "constructors_champion": "Constructors' Champion",
    "most_race_wins": "Most Race Wins",
    "most_podiums": "Most Podiums",
    "first_driver_sacked": "First Driver Sacked",
    "most_crashes": "Most Crashes",
    "last_place_driver": "Last Place Driver"
}

# Database pool
async def create_db_pool():
    return await asyncpg.create_pool(DATABASE_URL)

async def init_db(pool):
    async with pool.acquire() as conn:
        await conn.execute('SET SESSION CHARACTERISTICS AS TRANSACTION ISOLATION LEVEL SERIALIZABLE')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS report_channels (
                guild_id BIGINT PRIMARY KEY,
                channel_id BIGINT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id SERIAL PRIMARY KEY,
                guild_id BIGINT,
                user_id BIGINT,
                message TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                timezone TEXT,
                favorite_driver TEXT,
                favorite_team TEXT,
                favorite_track TEXT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_points (
                guild_id BIGINT,
                user_id BIGINT,
                points INTEGER DEFAULT 0,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS season_predictions (
                guild_id BIGINT,
                user_id BIGINT,
                category TEXT,
                prediction TEXT,
                PRIMARY KEY (guild_id, user_id, category),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS race_predictions (
                guild_id BIGINT,
                race_name TEXT,
                user_id BIGINT,
                category TEXT,
                prediction TEXT,
                PRIMARY KEY (guild_id, race_name, user_id, category),
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS guilds (
                guild_id BIGINT PRIMARY KEY,
                reminder_channel_id BIGINT
            )
        ''')
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS user_reminders (
                guild_id BIGINT,
                user_id BIGINT,
                event_name TEXT,
                PRIMARY KEY (guild_id, user_id),
                FOREIGN KEY (guild_id) REFERENCES guilds(guild_id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE
            )
        ''')

# Database helpers
async def get_user_timezone(pool, user_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT timezone FROM users WHERE user_id = $1', user_id)
        return pytz.timezone(row['timezone']) if row and row['timezone'] else pytz.UTC

async def get_reminder_channel(pool, guild_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT reminder_channel_id FROM guilds WHERE guild_id = $1', guild_id)
        return row['reminder_channel_id'] if row else None

async def get_guild_reminders(pool, guild_id):
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id, event_name FROM user_reminders WHERE guild_id = $1', guild_id)
        return dict(rows)

async def get_user_data(pool, user_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT timezone, favorite_driver, favorite_team, favorite_track FROM users WHERE user_id = $1', user_id)
        if row:
            return {
                'timezone': pytz.timezone(row['timezone']) if row['timezone'] else pytz.UTC,
                'favorite_driver': row['favorite_driver'],
                'favorite_team': row['favorite_team'],
                'favorite_track': row['favorite_track']
            }
        return {'timezone': pytz.UTC, 'favorite_driver': None, 'favorite_team': None, 'favorite_track': None}

async def get_user_points(pool, guild_id, user_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow('SELECT points FROM user_points WHERE guild_id = $1 AND user_id = $2', guild_id, user_id)
        return row['points'] if row else 0

async def get_season_predictions(pool, guild_id, user_id):
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT category, prediction FROM season_predictions WHERE guild_id = $1 AND user_id = $2', guild_id, user_id)
        return dict(rows)

async def get_race_predictions(pool, guild_id, user_id):
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT race_name, category, prediction FROM race_predictions WHERE guild_id = $1 AND user_id = $2', guild_id, user_id)
        predictions = {}
        for row in rows:
            if row['race_name'] not in predictions:
                predictions[row['race_name']] = {}
            predictions[row['race_name']][row['category']] = row['prediction']
        return predictions

async def get_leaderboard(pool, guild_id):
    async with pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id, points FROM user_points WHERE guild_id = $1 ORDER BY points DESC', guild_id)
        return [(row['user_id'], row['points'] or 0) for row in rows]

# Helper functions (unchanged)
async def get_weather(location):
    url = "http://api.openweathermap.org/data/2.5/weather"
    params = {"q": location, "appid": OPENWEATHERMAP_API_KEY, "units": "metric"}
    response = requests.get(url, params=params)
    if response.status_code == 200:
        return response.json()
    url = "http://api.weatherapi.com/v1/current.json"
    params = {"key": WEATHERAPI_API_KEY, "q": location}
    response = requests.get(url, params=params)
    return response.json() if response.status_code == 200 else None

async def geocode_location(location):
    url = "https://api.opencagedata.com/geocode/v1/json"
    params = {"q": location, "key": OPENCAGE_API_KEY}
    response = requests.get(url, params=params)
    if response.status_code == 200 and response.json()["results"]:
        return response.json()["results"][0]["geometry"]
    return None

def find_closest_race(user_input, races):
    user_input = user_input.lower().strip()
    best_match = None
    highest_score = 0
    for race in races:
        terms = [race["name"].lower()] + [kw.lower() for kw in race.get("keywords", [])]
        for term in terms:
            if user_input == term:
                print(f"Exact match: {race['name']}")
                return race["name"]
            score = process.fuzz.partial_ratio(user_input, term)
            if score > highest_score:
                highest_score = score
                best_match = race["name"]
    print(f"Best match: {best_match} ({highest_score}%)")
    return best_match if highest_score > 85 else None

def find_longest_match(words, candidates):
    best_match, best_score, best_length = None, 0, 0
    for i in range(1, len(words) + 1):
        prefix = " ".join(words[:i])
        for candidate in candidates:
            score = process.extractOne(prefix.lower(), [candidate.lower()])[1]
            if score > best_score or (score == best_score and i > best_length):
                best_score, best_match, best_length = score, candidate, i
    return (best_length, best_match) if best_score >= 80 else (0, None)

def validate_podium(prediction, correct_answer):
    try:
        pred_match = re.match(r'2\.\s*(.+?)\s+3\.\s*(.+)', prediction)
        corr_match = re.match(r'2\.\s*(.+?)\s+3\.\s*(.+)', correct_answer)
        if not pred_match or not corr_match:
            return 0
        pred_drivers = [pred_match.group(1).strip().lower(), pred_match.group(2).strip().lower()]
        corr_drivers = [corr_match.group(1).strip().lower(), corr_match.group(2).strip().lower()]
        correct = 0
        for i in range(2):
            if pred_drivers[i] == corr_drivers[i]:
                correct += 1
        return correct
    except Exception as e:
        print(f"Error validating podium: {e}")
        return 0

def get_race_start_time(race_name):
    for event in f1_calendar:
        if event["name"].lower() == race_name.lower():
            gp_time = event["sessions"].get("gp")
            if gp_time:
                return datetime.fromisoformat(gp_time.replace("Z", "+00:00"))
    return None

def get_next_race(current_race_name):
    races = f1_calendar
    current_index = next((i for i, race in enumerate(races) if race["name"] == current_race_name), None)
    if current_index is not None and current_index + 1 < len(races):
        return races[current_index + 1]["name"]
    return None

class RacePrediction:
    def __init__(self, user_id, race_name, category, prediction):
        self.user_id = user_id
        self.race_name = race_name
        self.category = category
        self.prediction = prediction

    def validate(self, correct_answer):
        if self.category == RaceCategory.PODIUM.value:
            return validate_podium(self.prediction, correct_answer)
        return 1 if self.prediction.lower() == correct_answer.lower() else 0

def get_closest_driver(input_name):
    input_lower = input_name.strip().lower()
    matches = process.extract(input_lower, VALID_DRIVERS_LOWER, limit=1)
    if not matches:
        return None
    best_match, score = matches[0]
    if score >= 80:
        return VALID_DRIVERS[VALID_DRIVERS_LOWER.index(best_match)]
    return None

def get_closest_team(input_name):
    input_lower = input_name.strip().lower()
    matches = process.extract(input_lower, VALID_TEAMS_LOWER, limit=1)
    if not matches:
        return None
    best_match, score = matches[0]
    if score >= 80:
        return VALID_TEAMS[VALID_TEAMS_LOWER.index(best_match)]
    return None

# Bot events
@bot.event
async def on_ready():
    bot.db_pool = await create_db_pool()
    await init_db(bot.db_pool)
    print(f'Logged in as {bot.user}')
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.playing, name="!help | Donate: https://ko-fi.com/gridguru"))
    session_reminder.start()
    prediction_reminder.start()

@tasks.loop(minutes=1)
async def prediction_reminder():
    now = datetime.now(pytz.UTC)
    for event in f1_calendar:
        gp_time = datetime.fromisoformat(event["sessions"]["gp"].replace("Z", "+00:00"))
        time_difference = gp_time - now
        if timedelta(minutes=30) <= time_difference < timedelta(minutes=31):
            async with bot.db_pool.acquire() as conn:
                rows = await conn.fetch('SELECT guild_id, reminder_channel_id FROM guilds WHERE reminder_channel_id IS NOT NULL')
            for row in rows:
                channel = bot.get_channel(row['reminder_channel_id'])
                if channel:
                    await channel.send(
                        f"⏰ **Predictions for {event['name']} close in 30 minutes!**\n"
                        f"Use `!predictrace {event['name']} <category> <prediction>` to make your predictions!"
                    )

# Timezone commands
@bot.command()
async def settimezone(ctx, timezone: str):
    timezone = timezone.upper()
    if timezone in timezone_mapping:
        timezone = timezone_mapping[timezone]
    try:
        pytz.timezone(timezone)
        async with bot.db_pool.acquire() as conn:
            await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING', ctx.author.id)
            await conn.execute('UPDATE users SET timezone = $1 WHERE user_id = $2', timezone, ctx.author.id)
        await ctx.send(f"Your timezone has been set to **{timezone}**.")
    except pytz.UnknownTimeZoneError:
        await ctx.send("Invalid timezone. Use a valid timezone (e.g., `EST`, `GMT`, `Europe/London`).")

@bot.command()
async def mytimezone(ctx):
    user_timezone = await get_user_timezone(bot.db_pool, ctx.author.id)
    await ctx.send(f"Your current timezone is **{user_timezone.zone}**.")

# Session commands
@bot.command()
async def nextsession(ctx):
    now = datetime.now(pytz.UTC)
    next_session = None
    for event in f1_calendar:
        for session_name, session_time in event["sessions"].items():
            session_time = datetime.fromisoformat(session_time.replace("Z", "+00:00"))
            if session_time > now and (next_session is None or session_time < next_session["time"]):
                next_session = {"event": event["name"], "session": session_name, "time": session_time, "track_timezone": pytz.timezone(event["timezone"])}
    if next_session:
        user_timezone = await get_user_timezone(bot.db_pool, ctx.author.id)
        local_time = next_session["time"].astimezone(user_timezone)
        track_time = next_session["time"].astimezone(next_session["track_timezone"])
        embed = discord.Embed(title=next_session["event"], description=f"**{next_session['session'].upper()}**", color=discord.Color.blue())
        embed.add_field(name="Track Time", value=track_time.strftime('%Y-%m-%d %H:%M'), inline=False)
        embed.add_field(name="Your Time", value=local_time.strftime('%Y-%m-%d %H:%M'), inline=False)
        embed.add_field(name="Timezone", value=user_timezone.zone, inline=False)
        await ctx.send(embed=embed)
    else:
        await ctx.send("No upcoming sessions found.")

@bot.command()
async def fullschedule(ctx):
    user_timezone = await get_user_timezone(bot.db_pool, ctx.author.id)
    embed = discord.Embed(title="2025 Formula 1 Calendar", description="All times are in your local time.", color=discord.Color.green())
    for event in f1_calendar:
        gp_time = datetime.fromisoformat(event["sessions"]["gp"].replace("Z", "+00:00")).astimezone(user_timezone)
        embed.add_field(name=event["name"], value=gp_time.strftime('%Y-%m-%d %H:%M'), inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def nextthree(ctx):
    now = datetime.now(pytz.UTC)
    user_timezone = await get_user_timezone(bot.db_pool, ctx.author.id)
    embed = discord.Embed(title="Next 3 F1 Events", color=discord.Color.orange())
    upcoming_events = []
    for event in f1_calendar:
        gp_time = datetime.fromisoformat(event["sessions"]["gp"].replace("Z", "+00:00"))
        if gp_time > now and len(upcoming_events) < 3:
            local_time = gp_time.astimezone(user_timezone)
            track_time = gp_time.astimezone(pytz.timezone(event["timezone"]))
            upcoming_events.append((event["name"], track_time.strftime('%Y-%m-%d %H:%M'), local_time.strftime('%Y-%m-%d %H:%M')))
    if upcoming_events:
        for event_name, track_time, local_time in upcoming_events:
            embed.add_field(name=event_name, value=f"Track Time: {track_time}\nYour Time: {local_time}", inline=False)
        await ctx.send(embed=embed)
    else:
        await ctx.send("No upcoming events found.")

@bot.command()
async def event(ctx, *, search_term: str):
    search_term = search_term.strip().lower()
    matching_events = [e for e in f1_calendar if search_term in e["name"].lower() or search_term in [kw.lower() for kw in e.get("keywords", [])]]
    if not matching_events:
        await ctx.send(f"No events found matching '{search_term}'.")
        return
    if len(matching_events) > 1:
        event_list = "\n".join([e["name"] for e in matching_events])
        await ctx.send(f"Multiple events found. Please be more specific:\n{event_list}")
        return
    event = matching_events[0]
    user_timezone = await get_user_timezone(bot.db_pool, ctx.author.id)
    embed = discord.Embed(title=f"{event['name']} Sessions", description="All times are in your local time.", color=discord.Color.purple())
    for session, session_time in event["sessions"].items():
        local_time = datetime.fromisoformat(session_time.replace("Z", "+00:00")).astimezone(user_timezone)
        embed.add_field(name=session.upper(), value=local_time.strftime('%Y-%m-%d %H:%M'), inline=False)
    await ctx.send(embed=embed)

# Reminder commands
@bot.command()
@commands.has_permissions(manage_guild=True)
async def setreminderchannel(ctx, channel: discord.TextChannel):
    async with bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO guilds (guild_id, reminder_channel_id) VALUES ($1, $2) ON CONFLICT (guild_id) DO UPDATE SET reminder_channel_id = $2', ctx.guild.id, channel.id)
    await ctx.send(f"Reminders will be sent to {channel.mention}.")

@bot.command()
async def checkreminderchannel(ctx):
    channel_id = await get_reminder_channel(bot.db_pool, ctx.guild.id)
    if channel_id:
        channel = bot.get_channel(channel_id)
        if channel:
            await ctx.send(f"Reminders are set to {channel.mention}.")
        else:
            await ctx.send("The reminder channel no longer exists. Set a new one with `!setreminderchannel`.")
    else:
        await ctx.send("No reminder channel set. Use `!setreminderchannel` to set one.")

@bot.command()
async def reminder(ctx, *, search_term: str):
    search_term = search_term.strip().lower()
    matching_events = [e for e in f1_calendar if search_term in e["name"].lower() or search_term in e.get("keywords", [])]]
    if not matching_events:
        await ctx.send(f"No events found matching '{search_term}'.")
        return
    if len(matching_events) > 1:
        event_list = "\n".join([e["name"] for e in matching_events])
        await ctx.send(f"Multiple events found. Please be more specific:\n{event_list}")
        return
    event = matching_events[0]
    async with bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO user_reminders (guild_id, user_id, event_name) VALUES ($1, $2, $3) ON CONFLICT (guild_id, user_id) DO UPDATE SET event_name = $3', ctx.guild.id, ctx.author.id, event["name"])
    await ctx.send(f"You will be reminded about the **{event['name']}**.")

@tasks.loop(minutes=5)
async def session_reminder():
    try:
        now = datetime.now(pytz.UTC)
        print(f"Current time (UTC): {now}")
        for event in f1_calendar:
            for session_name, session_time in event["sessions"].items():
                session_time = datetime.fromisoformat(session_time.replace("Z", "+00:00"))
                time_difference = session_time - now
                if 0 < time_difference.total_seconds() <= 3600:  # 1 hour
                    print(f"Session found: {event['name']} - {session_name} at {session_time}")
                    async with bot.db_pool.acquire() as conn:
                        guilds = await conn.fetch('SELECT guild_id, reminder_channel_id FROM guilds WHERE reminder_channel_id IS NOT NULL')
                    for guild in guilds:
                        channel = bot.get_channel(guild['reminder_channel_id'])
                        if channel:
                            guild_reminders = await get_guild_reminders(bot.db_pool, guild['guild_id'])
                            users_to_ping = [f"<@{user_id}>" for user_id, event_name in guild_reminders.items() if event_name == event["name"]]
                            print(f"Users to ping in guild {guild['guild_id']}: {users_to_ping}")
                            if users_to_ping:
                                await channel.send(" ".join(users_to_ping))
                            embed = discord.Embed(title=f"⏰ Reminder: {event['name']} - {session_name.upper()}", description="The session starts in **1 hour**!", color=discord.Color.blue())
                            await channel.send(embed=embed)
    except discord.HTTPException as e:
        if e.status == 429:
            retry_after = e.retry_after
            await asyncio.sleep(retry_after)

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"Slow down! Try again in {error.retry_after:.1f}s")
    elif isinstance(error, discord.HTTPException) and error.status == 429:
        await ctx.send("Rate limited. Please try again later.")
        await asyncio.sleep(error.retry_after)

# Weather command
@bot.command()
async def weather(ctx, *, search_term: str):
    search_term = search_term.strip().lower()
    matching_events = [e for e in f1_calendar if search_term in e["name"].lower() or search_term in e.get("keywords", [])]]
    if not matching_events:
        await ctx.send(f"No events found matching '{search_term}'.")
        return
    if len(matching_events) > 1:
        event_list = "\n".join([e["name"] for e in matching_events])
        await ctx.send(f"Multiple events found. Please be more specific:\n{event_list}")
        return
    event = matching_events[0]
    location = event["location"]
    weather_data = await get_weather(location)
    if not weather_data:
        coords = await geocode_location(location)
        if coords:
            weather_data = await get_weather(f"{coords['lat']},{coords['lng']}")
    if not weather_data:
        await ctx.send(f"Sorry, I couldn't find weather data for {location}.")
        return
    if "main" in weather_data:
        temp, desc, humidity, wind = weather_data["main"]["temp"], weather_data["weather"][0]["description"], weather_data["main"]["humidity"], weather_data["wind"]["speed"]
    else:
        temp, desc, humidity, wind = weather_data["current"]["temp_c"], weather_data["current"]["condition"]["text"], weather_data["current"]["humidity"], weather_data["current"]["wind_kph"]
    embed = discord.Embed(title=f"🌤️ Weather for {event['name']}", description=f"Location: {location}", color=discord.Color.blue())
    embed.add_field(name="Temperature", value=f"{temp}°C", inline=False)
    embed.add_field(name="Conditions", value=desc, inline=False)
    embed.add_field(name="Humidity", value=f"{humidity}%", inline=False)
    embed.add_field(name="Wind Speed", value=f"{wind} m/s", inline=False)
    await ctx.send(embed=embed)

# Favorite commands
@bot.command()
async def setfavoriteteam(ctx, *, team: str):
    async with bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING', ctx.author.id)
        await conn.execute('UPDATE users SET favorite_team = $1 WHERE user_id = $2', team, ctx.author.id)
    await ctx.send(f"Your favorite team has been set to **{team}**!")

@bot.command()
async def setfavoritedriver(ctx, *, driver: str):
    async with bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING', ctx.author.id)
        await conn.execute('UPDATE users SET favorite_driver = $1 WHERE user_id = $2', driver, ctx.author.id)
    await ctx.send(f"Your favorite driver has been set to **{driver}**!")

@bot.command()
async def setfavoritetrack(ctx, *, track: str):
    async with bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING', ctx.author.id)
        await conn.execute('UPDATE users SET favorite_track = $1 WHERE user_id = $2', track, ctx.author.id)
    await ctx.send(f"Your favorite track has been set to **{track}**!")

@bot.command()
async def resetfavorites(ctx):
    async with bot.db_pool.acquire() as conn:
        await conn.execute('UPDATE users SET favorite_team = NULL, favorite_driver = NULL, favorite_track = NULL WHERE user_id = $1', ctx.author.id)
    await ctx.send("Your favorites have been reset!")

# Profile command
@bot.command()
async def profile(ctx, user: discord.Member = None):
    target_user = user or ctx.author
    user_data = await get_user_data(bot.db_pool, target_user.id)
    points = await get_user_points(bot.db_pool, ctx.guild.id, target_user.id)
    season_preds = await get_season_predictions(bot.db_pool, ctx.guild.id, target_user.id)
    race_preds = await get_race_predictions(bot.db_pool, ctx.guild.id, target_user.id)

    embed = discord.Embed(
        title=f"{target_user.display_name}'s Profile (Server: {ctx.guild.name})",
        color=discord.Color.blue()
    )
    embed.add_field(name="⏰ Timezone", value=user_data['timezone'].zone if user_data['timezone'] else "Not set", inline=False)
    embed.add_field(name="🏎️ Favorite Team", value=user_data['favorite_team'] or "Not set", inline=True)
    embed.add_field(name="👤 Favorite Driver", value=user_data['favorite_driver'] or "Not set", inline=True)
    embed.add_field(name="🏁 Favorite Track", value=user_data['favorite_track'] or "Not set", inline=True)
    total_predictions = len(season_preds) + sum(len(preds) for preds in race_preds.values())
    embed.add_field(name="📊 Stats", value=f"**Points:** {points}\n**Total Predictions:** {total_predictions}", inline=False)
    if season_preds:
        season_text = "\n".join(f"• **{CATEGORY_DISPLAY_NAMES.get(cat, cat.replace('_', ' ').title())}:** {pred}" for cat, pred in season_preds.items())
        embed.add_field(name="🔮 Season Predictions", value=season_text, inline=False)
    if race_preds:
        race_text = []
        for race_name, categories in race_preds.items():
            race_entry = [f"**{race_name}**"]
            race_entry.extend(f"- {CATEGORY_DISPLAY_NAMES.get(cat, cat.replace('_', ' ').title())}: {pred}" for cat, pred in categories.items())
            race_text.append("\n".join(race_entry))
        embed.add_field(name="🏁 Race Predictions", value="\n\n".join(race_text) if race_text else "No race predictions", inline=False)
    embed.set_thumbnail(url=target_user.display_avatar.url)
    await ctx.send(embed=embed)

# Prediction commands
@bot.command()
async def predictseason(ctx, category: str, *, prediction: str):
    guild_id, user_id = ctx.guild.id, ctx.author.id
    valid_categories = ["drivers_champion", "constructors_champion", "most_race_wins", "most_podiums", "first_driver_sacked", "most_crashes", "last_place_driver"]
    category = category.lower()
    if category not in valid_categories:
        await ctx.send(f"Invalid category. Valid options: {', '.join(valid_categories)}")
        return
    if category in ["drivers_champion", "most_race_wins", "most_podiums", "first_driver_sacked", "most_crashes", "last_place_driver"]:
        closest = get_closest_driver(prediction)
        if not closest:
            await ctx.send(f"Invalid driver: {prediction}. Valid drivers: {', '.join(VALID_DRIVERS)}")
            return
        prediction = closest
    elif category == "constructors_champion":
        closest = get_closest_team(prediction)
        if not closest:
            await ctx.send(f"Invalid team: {prediction}. Valid teams: {', '.join(VALID_TEAMS)}")
            return
        prediction = closest
    async with bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO season_predictions (guild_id, user_id, category, prediction) VALUES ($1, $2, $3, $4) ON CONFLICT (guild_id, user_id, category) DO UPDATE SET prediction = $4', guild_id, user_id, category, prediction)
    await ctx.send(f"Prediction saved for {category.replace('_', ' ').title()}!")

@bot.command()
async def predictseasonbulk(ctx, *, predictions: str):
    guild_id, user_id = ctx.guild.id, ctx.author.id
    valid_categories = ["drivers_champion", "constructors_champion", "most_race_wins", "most_podiums", "first_driver_sacked", "most_crashes", "last_place_driver"]
    try:
        predictions_dict = {}
        for item in predictions.split(","):
            category, prediction = item.strip().split(":")
            category, prediction = category.strip().lower(), prediction.strip()
            if category not in valid_categories:
                await ctx.send(f"Invalid category: {category}. Valid categories: {', '.join(valid_categories)}")
                return
            if category in ["drivers_champion", "most_race_wins", "most_podiums", "first_driver_sacked", "most_crashes", "last_place_driver"]:
                if prediction.lower() not in VALID_DRIVERS_LOWER:
                    await ctx.send(f"Invalid driver: {prediction}. Valid drivers: {', '.join(VALID_DRIVERS)}")
                    return
            elif category == "constructors_champion":
                if prediction.lower() not in VALID_TEAMS_LOWER:
                    await ctx.send(f"Invalid team: {prediction}. Valid teams: {', '.join(VALID_TEAMS)}")
                    return
            predictions_dict[category] = prediction
        async with bot.db_pool.acquire() as conn:
            for category, prediction in predictions_dict.items():
                await conn.execute('INSERT INTO season_predictions (guild_id, user_id, category, prediction) VALUES ($1, $2, $3, $4) ON CONFLICT (guild_id, user_id, category) DO UPDATE SET prediction = $4', guild_id, user_id, category, prediction)
        await ctx.send("Your season predictions have been saved!")
    except Exception as e:
        print(f"Error: {e}")
        await ctx.send("Failed to parse predictions. Use: `!predictseasonbulk drivers_champion: Verstappen, constructors_champion: Red Bull`")

@bot.command()
@commands.cooldown(2, 30, commands.BucketType.user)
async def predictrace(ctx, *, input: str):
    guild_id, user_id = ctx.guild.id, ctx.author.id
    closest_race = find_closest_race(input, f1_calendar)
    if not closest_race:
        await ctx.send("Could not find a matching race.")
        return
    pattern = re.compile(re.escape(closest_race), re.IGNORECASE)
    remaining_input = pattern.sub("", input, 1).strip()
    print(f"Original input: {input}")
    print(f"Remaining after race removal: '{remaining_input}'")
    if not remaining_input:
        await ctx.send("Please provide both a category and prediction.")
        return
    words = remaining_input.lower().split()
    category = None
    for cat, aliases in CATEGORY_FUZZY_MAP.items():
        for alias in aliases:
            if alias in remaining_input.lower():
                category = cat
                start_index = remaining_input.lower().find(alias)
                prediction = remaining_input[start_index+len(alias):].strip()
                break
        if category:
            break
    if not category:
        await ctx.send("Could not detect category. Use: 'race_winner', 'pole_sitter', etc.")
        return
    print(f"Detected category: {category}")
    print(f"Raw prediction: {prediction}")
    if not prediction:
        await ctx.send("Please provide a prediction after the category.")
        return
    if category == RaceCategory.PODIUM.value:
        podium_drivers = prediction.split()
        if len(podium_drivers) != 2:
            await ctx.send("Podium predictions must include exactly 2 drivers (2nd and 3rd place).")
            return
        corrected_drivers = []
        for driver in podium_drivers:
            closest = get_closest_driver(driver)
            if not closest:
                await ctx.send(f"Invalid driver: {driver}. Valid drivers: {', '.join(VALID_DRIVERS)}")
                return
            corrected_drivers.append(closest)
        prediction = f"2. {corrected_drivers[0]} 3. {corrected_drivers[1]}"
    if category in [RaceCategory.RACE_WINNER.value, RaceCategory.POLE_SITTER.value, RaceCategory.FIRST_CRASH.value]:
        closest = get_closest_driver(prediction)
        if not closest:
            await ctx.send(f"Invalid driver: {prediction}. Valid drivers: {', '.join(VALID_DRIVERS)}")
            return
        prediction = closest
    race_start_time = get_race_start_time(closest_race)
    if race_start_time and datetime.now(pytz.UTC) >= race_start_time - timedelta(minutes=10):
        await ctx.send("Predictions are closed for this race (closes 10 minutes before start).")
        return
    display_category = CATEGORY_DISPLAY_NAMES.get(category, category.replace('_', ' ').title())
    confirm_message = await ctx.send(
        f"Are you sure you want to predict **{prediction}** for **{display_category}** in **{closest_race}**? (✅ to confirm, ❌ to cancel)",
        delete_after=30
    )
    await confirm_message.add_reaction("✅")
    await confirm_message.add_reaction("❌")
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅", "❌"]
    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=check)
        if str(reaction.emoji) == "✅":
            async with bot.db_pool.acquire() as conn:
                await conn.execute('INSERT INTO race_predictions (guild_id, race_name, user_id, category, prediction) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (guild_id, race_name, user_id, category) DO UPDATE SET prediction = $5', guild_id, closest_race, user_id, category, prediction)
            await ctx.send("Prediction saved!", delete_after=10)
        else:
            await ctx.send("Prediction canceled.")
    except asyncio.TimeoutError:
        await ctx.send("Prediction timed out. Try again.")

@bot.command()
async def editprediction(ctx, race_name: str, category: str, *, prediction: str):
    guild_id, user_id = ctx.guild.id, ctx.author.id
    closest_race = find_closest_race(race_name, f1_calendar)
    if not closest_race:
        await ctx.send("Could not find a matching race.")
        return
    if category not in RaceCategory._value2member_map_:
        await ctx.send("Invalid category. Use: race_winner, pole_sitter, podium, first_crash")
        return
    race_start_time = get_race_start_time(closest_race)
    if race_start_time and datetime.now(pytz.UTC) >= race_start_time - timedelta(minutes=10):
        await ctx.send("Predictions can no longer be edited for this race (closes 10 minutes before start).")
        return
    display_category = CATEGORY_DISPLAY_NAMES.get(category, category.replace('_', ' ').title())
    confirm_message = await ctx.send(f"Are you sure you want to edit your prediction to **{prediction}** for **{display_category}** in **{closest_race}**? (✅ to confirm, ❌ to cancel)")
    await confirm_message.add_reaction("✅")
    await confirm_message.add_reaction("❌")
    def check(reaction, user):
        return user == ctx.author and str(reaction.emoji) in ["✅", "❌"]
    try:
        reaction, _ = await bot.wait_for("reaction_add", timeout=30.0, check=check)
        if str(reaction.emoji) == "✅":
            async with bot.db_pool.acquire() as conn:
                await conn.execute('INSERT INTO race_predictions (guild_id, race_name, user_id, category, prediction) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (guild_id, race_name, user_id, category) DO UPDATE SET prediction = $5', guild_id, closest_race, user_id, category, prediction)
            await ctx.send("Prediction updated!")
        else:
            await ctx.send("Edit canceled.")
    except asyncio.TimeoutError:
        await ctx.send("Edit timed out. Try again.")

@bot.command()
async def mypredictions(ctx):
    guild_id, user_id = ctx.guild.id, ctx.author.id
    season_preds = await get_season_predictions(bot.db_pool, guild_id, user_id)
    race_preds = await get_race_predictions(bot.db_pool, guild_id, user_id)
    user_predictions = [f"**Season** - {CATEGORY_DISPLAY_NAMES.get(cat, cat.replace('_', ' ').title())}: {pred}" for cat, pred in season_preds.items()]
    for race_name, preds in race_preds.items():
        user_predictions.extend(f"**{race_name}** - {CATEGORY_DISPLAY_NAMES.get(cat, cat.replace('_', ' ').title())}: {pred}" for cat, pred in preds.items())
    if user_predictions:
        embed = discord.Embed(title="Your Predictions in this Server", description="\n".join(user_predictions), color=discord.Color.blue())
        await ctx.send(embed=embed)
    else:
        await ctx.send("You have no predictions in this server.")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def validateseason(ctx, category: str, correct_answer: str):
    guild_id = ctx.guild.id
    valid_categories = ["drivers_champion", "constructors_champion", "most_race_wins", "most_podiums", "first_driver_sacked", "most_crashes", "last_place_driver"]
    category = category.lower()
    if category not in valid_categories:
        await ctx.send(f"Invalid category. Valid options: {', '.join(valid_categories)}")
        return
    if category in ["drivers_champion", "most_race_wins", "most_podiums", "first_driver_sacked", "most_crashes", "last_place_driver"]:
        closest = get_closest_driver(correct_answer)
        if not closest:
            await ctx.send(f"Invalid driver: {correct_answer}. Valid drivers: {', '.join(VALID_DRIVERS)}")
            return
        correct_answer = closest
    elif category == "constructors_champion":
        closest = get_closest_team(correct_answer)
        if not closest:
            await ctx.send(f"Invalid team: {correct_answer}. Valid teams: {', '.join(VALID_TEAMS)}")
            return
        correct_answer = closest
    async with bot.db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id, prediction FROM season_predictions WHERE guild_id = $1 AND category = $2', guild_id, category)
        if not rows:
            await ctx.send(f"No predictions found for {category.replace('_', ' ').title()}.")
            return
        correct_users = []
        for row in rows:
            user_id, prediction = row['user_id'], row['prediction']
            if prediction.lower() == correct_answer.lower():
                await conn.execute('INSERT INTO user_points (guild_id, user_id, points) VALUES ($1, $2, 10) ON CONFLICT (guild_id, user_id) DO UPDATE SET points = points + 10', guild_id, user_id)
                correct_users.append(user_id)
            await conn.execute('DELETE FROM season_predictions WHERE guild_id = $1 AND user_id = $2 AND category = $3', guild_id, user_id, category)
        if correct_users:
            await ctx.send(f"✅ Correct predictions for **{category.replace('_', ' ').title()}**:\n" f"{', '.join(f'<@{user_id}>' for user_id in correct_users)}")
        else:
            await ctx.send(f"❌ No correct predictions for **{category.replace('_', ' ').title()}**.")

@bot.command()
@commands.has_permissions(administrator=True)
async def validaterace(ctx, race_name: str, category: str, *, correct_answer: str):
    guild_id = ctx.guild.id
    closest_race = find_closest_race(race_name, f1_calendar)
    if not closest_race:
        await ctx.send("Could not find a matching race.")
        return
    if category not in RaceCategory._value2member_map_:
        await ctx.send("Invalid category. Use: race_winner, pole_sitter, podium, first_crash")
        return
    if category == "podium" and len(correct_answer.split()) == 2:
        drivers = correct_answer.split()
        correct_answer = f"2. {drivers[0]} 3. {drivers[1]}"
    async with bot.db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT user_id, prediction FROM race_predictions WHERE guild_id = $1 AND race_name = $2 AND category = $3', guild_id, closest_race, category)
        if not rows:
            await ctx.send(f"No predictions found for **{closest_race} - {category.replace('_', ' ').title()}**.")
            return
        correct_users = []
        for row in rows:
            user_id, prediction = row['user_id'], row['prediction']
            pred = RacePrediction(user_id, closest_race, category, prediction)
            points = pred.validate(correct_answer)
            if points > 0:
                await conn.execute('INSERT INTO user_points (guild_id, user_id, points) VALUES ($1, $2, $3) ON CONFLICT (guild_id, user_id) DO UPDATE SET points = points + $3', guild_id, user_id, points)
                correct_users.append((user_id, points))
            await conn.execute('DELETE FROM race_predictions WHERE guild_id = $1 AND race_name = $2 AND user_id = $3 AND category = $4', guild_id, closest_race, user_id, category)
        next_race = get_next_race(closest_race)
        if next_race:
            await ctx.send(f"Predictions are now open for the next race: **{next_race}**!")
        else:
            await ctx.send("This was the last race of the season. Predictions are now closed.")
        if correct_users:
            await ctx.send(f"✅ Correct predictions for **{closest_race} - {category.replace('_', ' ').title()}**:\n" f"{', '.join(f'<@{user_id}> (+{points} points)' for user_id, points in correct_users)}")
        else:
            await ctx.send(f"❌ No correct predictions for **{closest_race} - {category.replace('_', ' ').title()}**.")

@bot.command()
async def leaderboard(ctx):
    leaderboard_data = await get_leaderboard(bot.db_pool, ctx.guild.id)
    if not leaderboard_data:
        await ctx.send("No leaderboard data available for this server.")
        return
    leaderboard_text = "\n".join(f"{idx + 1}. <@{user_id}> - {points} points" for idx, (user_id, points) in enumerate(leaderboard_data))
    embed = discord.Embed(title="🏆 Server Leaderboard", description=leaderboard_text, color=discord.Color.gold())
    await ctx.send(embed=embed)

@bot.command()
async def countdown(ctx):
    now = datetime.now(pytz.UTC)
    next_session = None
    for event in f1_calendar:
        for session_name, session_time in event["sessions"].items():
            session_time = datetime.fromisoformat(session_time.replace("Z", "+00:00"))
            if session_time > now and (next_session is None or session_time < next_session["time"]):
                next_session = {"event": event["name"], "session": session_name, "time": session_time}
    if next_session:
        time_difference = next_session["time"] - now
        days = time_difference.days
        hours, remainder = divmod(time_difference.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        embed = discord.Embed(
            title=f"⏳ Countdown to {next_session['event']} - {next_session['session'].upper()}",
            description=f"**{days} days, {hours} hours, {minutes} minutes, {seconds} seconds**",
            color=discord.Color.gold()
        )
        await ctx.send(embed=embed)
    else:
        await ctx.send("No upcoming sessions found.")

@bot.command()
async def myreminders(ctx):
    async with bot.db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT event_name FROM user_reminders WHERE guild_id = $1 AND user_id = $2', ctx.guild.id, ctx.author.id)
        active_reminders = [row['event_name'] for row in rows]
    if active_reminders:
        embed = discord.Embed(title="Your Active Reminders", description="\n".join(active_reminders), color=discord.Color.blue())
        await ctx.send(embed=embed)
    else:
        await ctx.send("You have no active reminders in this server.")

@bot.command()
@commands.has_permissions(manage_guild=True)
async def reloadcalendar(ctx):
    global f1_calendar
    f1_calendar = load_f1_calendar()
    await ctx.send("F1 calendar reloaded!")

@bot.command()
async def help(ctx):
    owner = await bot.fetch_user(BOT_OWNER_ID)
    commands_list = [
        ("General Commands", [
            ("!settimezone <timezone>", "Set your timezone (e.g., `EST` or `Europe/London`)"),
            ("!mytimezone", "Check your current timezone"),
            ("!nextsession", "Show next F1 session"),
            ("!fullschedule", "Full 2025 calendar"),
            ("!nextthree", "Next 3 events"),
            ("!event <Grand Prix>", "Show event sessions"),
            ("!countdown", "Countdown to next session"),
            ("!weather <Grand Prix>", "Event weather forecast")
        ]),
        ("Profile Commands", [
            ("!profile", "Display your profile"),
            ("!profile [@user]", "View your or another user's profile"),
            ("!setfavoriteteam <team>", "Set favorite team"),
            ("!setfavoritedriver <driver>", "Set favorite driver"),
            ("!setfavoritetrack <track>", "Set favorite track"),
            ("!resetfavorites", "Clear all favorites")
        ]),
        ("Prediction System", [
            ("!predictseason <category> <prediction>", "Make season prediction\n`Ex: !predictseason drivers_champion Verstappen`"),
            ("!predictseasonbulk <predictions>", "Bulk season predictions\n`Ex: drivers_champion:Verstappen, constructors_champion:RedBull`"),
            ("!predictrace <race> <category> <prediction>", "Make race prediction"),
            ("!editprediction <race> <category> <prediction>", "Edit race prediction"),
            ("!validatepredictions [@user]", "Check validity of predictions"),
            ("!mypredictions", "View your predictions")
        ]),
        ("Admin Commands", [
            ("!editprofile @user <field> <value>", "Edit user profiles\nFields: points, driver, team, track, timezone\n`Ex: !editprofile @User season_prediction \"drivers_champion; Verstappen\"`"),
            ("!resetprofile @user", "Full profile reset"),
            ("!validateseason <category> <answer>", "Validate season predictions"),
            ("!validaterace <race> <category> <answer>", "Validate race predictions"),
            ("!nuke_predictions", "Delete ALL season predictions"),
            ("!editpoints @user <points>", "Modify user points"),
            ("!setreminderchannel <channel>", "Set reminders channel"),
            ("!viewreports [limit]", "View recent reports (admin only)"),
            ("!setreportchannel <channel>", "Set where reports are logged")
        ]),
        ("Support", [
            ("!donate", "Support bot development"),
            ("!predicthelp", "Detailed prediction help"),
            ("!report <message>", "Report issues to server staff and bot developer")
        ])
    ]
    embed = discord.Embed(
        title="🏎️ F1 Bot Command Help",
        description="**Need more help?** Use `!predicthelp` for prediction details",
        color=discord.Color.blue()
    )
    for category_name, category_commands in commands_list:
        value = "\n".join(f"• **{cmd}** - {desc}" for cmd, desc in category_commands)
        embed.add_field(name=f"**{category_name}**", value=value, inline=False)
    embed.set_footer(text=f"Bot created by {owner.name} • Report issues with !report")
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(manage_guild=True)
async def viewreports(ctx, limit: int = 10):
    if limit < 1 or limit > 50:
        return await ctx.send("❌ Limit must be between 1 and 50")
    async with bot.db_pool.acquire() as conn:
        reports = await conn.fetch('SELECT id, user_id, message, timestamp FROM reports WHERE guild_id = $1 ORDER BY timestamp DESC LIMIT $2', ctx.guild.id, limit)
    if not reports:
        return await ctx.send("No reports found in this server")
    embed = discord.Embed(title=f"📝 Recent Reports ({len(reports)})", description=f"Showing up to {limit} most recent reports", color=0x00ff00)
    for report in reports:
        user = await bot.fetch_user(report['user_id'])
        embed.add_field(name=f"Report #{report['id']} • {report['timestamp']}", value=f"**User:** {user.mention}\n**Message:** {report['message']}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.cooldown(1, 300, commands.BucketType.user)
async def report(ctx, *, message: str):
    try:
        async with bot.db_pool.acquire() as conn:
            await conn.execute('INSERT INTO reports (guild_id, user_id, message) VALUES ($1, $2, $3)', ctx.guild.id, ctx.author.id, message)
            row = await conn.fetchrow('SELECT channel_id FROM report_channels WHERE guild_id = $1', ctx.guild.id)
        if row:
            channel = bot.get_channel(row['channel_id'])
            if channel:
                embed = discord.Embed(title="📝 Server Report", description=message, color=0xff0000)
                embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
                await channel.send(embed=embed)
        webhook = discord.Webhook.from_url(CENTRAL_REPORT_WEBHOOK, session=bot.http._HTTPClient__session)
        central_embed = discord.Embed(title="🌐 Cross-Server Report", description=message, color=0xff0000)
        central_embed.add_field(name="Server", value=f"{ctx.guild.name} ({ctx.guild.id})")
        central_embed.add_field(name="Channel", value=f"{ctx.channel.mention} ({ctx.channel.id})")
        central_embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
        await webhook.send(embed=central_embed)
        print(f"Webhook success: Report from {ctx.author}")
        await ctx.send("✅ Report submitted!", delete_after=10)
    except Exception as e:
        print(f"Report command failed: {str(e)}")
        await ctx.send("❌ Failed to process report", delete_after=10)

@bot.command()
async def predicthelp(ctx):
    embed = discord.Embed(title="Prediction System Help", description="Here’s how the prediction system works:", color=discord.Color.green())
    embed.add_field(name="Season-Long Predictions", value="You can predict outcomes for the entire season. Each correct prediction awards **10 points**.\n**Categories**:\n- `drivers_champion`: The driver who wins the championship.\n- `constructors_champion`: The team that wins the championship.\n- `most_race_wins`: The driver with the most race wins.\n- `most_podiums`: The driver with the most podium finishes.\n- `first_driver_sacked`: The first driver to be replaced during the season.\n- `most_crashes`: The driver with the most crashes.\n- `last_place_driver`: The driver who finishes last in the championship.\n**Example**:\n`!predictseasonbulk drivers_champion: Verstappen, constructors_champion: Red Bull, most_race_wins: Hamilton`", inline=False)
    embed.add_field(name="Race-Day Predictions", value="You can predict outcomes for each race weekend. Each correct prediction awards **1 point** (2 max for podium).\n**Categories**:\n- `race winner`, `winner`, or `who wins`: The driver who wins the race.\n- `pole sitter`, `pole`, or `fastest qualifier`: The driver who qualifies in pole position.\n- `podium`, `2nd and 3rd`, or `podium finishers`: The 2nd and 3rd place drivers (e.g., 'Norris Piastri').\n- `first crash`, `first out`, or `first dnf`: The driver involved in the first crash.\n**Example**:\n`!predictrace Australian Grand Prix race_winner Verstappen`", inline=False)
    embed.add_field(name="Leaderboard", value="Use `!leaderboard` to see the current standings based on points earned from predictions.", inline=False)
    embed.add_field(name="Admin Commands", value="Admins can validate predictions and award points using:\n- `!validateseason <category> <correct_answer>`: Validate season-long predictions.\n- `!validaterace <race_name> <category> <correct_answer>`: Validate race-day predictions.\n**Example**:\n`!validateseason drivers_champion Verstappen`\n`!validaterace Australian Grand Prix race_winner Verstappen`", inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def editpoints(ctx, user: discord.Member, points: int):
    async with bot.db_pool.acquire() as conn:
        await conn.execute('INSERT INTO user_points (guild_id, user_id, points) VALUES ($1, $2, $3) ON CONFLICT (guild_id, user_id) DO UPDATE SET points = $3', ctx.guild.id, user.id, points)
    embed = discord.Embed(title="Points Updated", description=f"{user.display_name}'s points have been updated to **{points}**.", color=discord.Color.green())
    await ctx.send(embed=embed)
    try:
        await user.send(f"Your points have been updated to **{points}** by an admin.")
    except discord.Forbidden:
        pass

@editpoints.error
async def editpoints_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You do not have permission to use this command.")
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Usage: `!editpoints @username <points>`")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Invalid user or points value. Please mention a user and provide a valid number.")
    else:
        await ctx.send(f"An error occurred: {error}")

@bot.command()
async def donate(ctx):
    embed = discord.Embed(title="Support F1 Bot", description="If you enjoy using this bot, consider supporting its development and server costs!", color=discord.Color.green())
    for platform, link in DONATION_LINKS.items():
        embed.add_field(name=platform, value=f"[Donate here]({link})", inline=False)
    await ctx.send(embed=embed)

@bot.command()
@commands.has_permissions(administrator=True)
async def resetprofile(ctx, user: discord.Member):
    guild_id, user_id = ctx.guild.id, user.id
    async with bot.db_pool.acquire() as conn:
        await conn.execute('DELETE FROM race_predictions WHERE guild_id = $1 AND user_id = $2', guild_id, user_id)
        await conn.execute('DELETE FROM season_predictions WHERE guild_id = $1 AND user_id = $2', guild_id, user_id)
        await conn.execute('DELETE FROM user_points WHERE guild_id = $1 AND user_id = $2', guild_id, user_id)
        await conn.execute('UPDATE users SET favorite_driver = NULL, favorite_team = NULL, favorite_track = NULL, timezone = NULL WHERE user_id = $1', user_id)
    await ctx.send(f"✅ Successfully reset <@{user_id}>'s profile.")

@bot.command()
@commands.has_permissions(administrator=True)
async def editprofile(ctx, user: discord.Member, field: str, *, value: str):
    guild_id, user_id = ctx.guild.id, user.id
    field = field.lower()
    async with bot.db_pool.acquire() as conn:
        if field == "points":
            try:
                points = int(value)
                await conn.execute('INSERT INTO user_points (guild_id, user_id, points) VALUES ($1, $2, $3) ON CONFLICT (guild_id, user_id) DO UPDATE SET points = $3', guild_id, user_id, points)
                await ctx.send(f"✅ Updated <@{user_id}>'s points to **{points}**.")
            except ValueError:
                await ctx.send("❌ Points must be a number.")
        elif field in ["driver", "team", "track"]:
            column = f"favorite_{field}"
            await conn.execute(f'UPDATE users SET {column} = $1 WHERE user_id = $2', value, user_id)
            await ctx.send(f"✅ Updated <@{user_id}>'s favorite {field} to **{value}**.")
        elif field == "timezone":
            if value in timezone_mapping:
                value = timezone_mapping[value]
            try:
                pytz.timezone(value)
                await conn.execute('UPDATE users SET timezone = $1 WHERE user_id = $2', value, user_id)
                await ctx.send(f"✅ Updated <@{user_id}>'s timezone to **{value}**.")
            except pytz.UnknownTimeZoneError:
                await ctx.send("❌ Invalid timezone. Use a valid timezone (e.g., `EST`, `GMT`, `Europe/London`).")
        elif field == "race_prediction":
            try:
                race_name, category, prediction = value.split(";")
                await conn.execute('INSERT INTO race_predictions (guild_id, race_name, user_id, category, prediction) VALUES ($1, $2, $3, $4, $5) ON CONFLICT (guild_id, race_name, user_id, category) DO UPDATE SET prediction = $5', guild_id, race_name.strip(), user_id, category.strip(), prediction.strip())
                await ctx.send(f"✅ Updated <@{user_id}>'s race prediction for **{race_name.strip()}** ({category.strip()}): **{prediction.strip()}**.")
            except ValueError:
                await ctx.send("❌ Invalid format. Use: `race_name; category; prediction`.")
        elif field == "season_prediction":
            try:
                category, prediction = value.split(";", 1)
                category = category.strip('"\'').lower().replace(" ", "_").replace("-", "_")
                prediction = prediction.strip()
                valid_categories = ["drivers_champion", "constructors_champion", "most_race_wins", "most_podiums", "first_driver_sacked", "most_crashes", "last_place_driver"]
                if category not in valid_categories:
                    await ctx.send(f"❌ Invalid category. Valid options: {', '.join(valid_categories)}")
                    return
                if category in ["drivers_champion", "most_race_wins", "most_podiums", "first_driver_sacked", "most_crashes", "last_place_driver"]:
                    closest = get_closest_driver(prediction)
                    if not closest:
                        await ctx.send(f"❌ Invalid driver: {prediction}. Valid drivers: {', '.join(VALID_DRIVERS)}")
                        return
                    prediction = closest
                elif category == "constructors_champion":
                    closest = get_closest_team(prediction)
                    if not closest:
                        await ctx.send(f"❌ Invalid team: {prediction}. Valid teams: {', '.join(VALID_TEAMS)}")
                        return
                    prediction = closest
                await conn.execute('INSERT INTO season_predictions (guild_id, user_id, category, prediction) VALUES ($1, $2, $3, $4) ON CONFLICT (guild_id, user_id, category) DO UPDATE SET prediction = $4', guild_id, user_id, category, prediction)
                await ctx.send(f"✅ Updated <@{user_id}>'s season prediction for **{category}**: **{prediction}**.")
            except ValueError:
                await ctx.send("❌ Invalid format. Use: `category; prediction`")

@bot.command()
@commands.has_permissions(administrator=True)
async def nuke_predictions(ctx):
    async with bot.db_pool.acquire() as conn:
        await conn.execute("DELETE FROM season_predictions")
    await ctx.send("✅ All season predictions deleted.")

@bot.command()
@commands.has_permissions(administrator=True)
async def debug_predictions(ctx, user: discord.Member):
    async with bot.db_pool.acquire() as conn:
        rows = await conn.fetch('SELECT * FROM season_predictions WHERE user_id = $1', user.id)

@bot.command()
async def validatepredictions(ctx, user: discord.Member = None):
    target_user = user or ctx.author
    valid_drivers = set(VALID_DRIVERS_LOWER)
    valid_teams = set(VALID_TEAMS_LOWER)
    season_preds = await get_season_predictions(bot.db_pool, ctx.guild.id, target_user.id)
    race_preds = await get_race_predictions(bot.db_pool, ctx.guild.id, target_user.id)
    embed = discord.Embed(title=f"🔍 Prediction Validation for {target_user.display_name}", color=discord.Color.gold())
    season_status = []
    for cat, pred in season_preds.items():
        pred_lower = pred.lower()
        valid = pred_lower in (valid_teams if cat == "constructors_champion" else valid_drivers)
        status = "✅" if valid else "❌"
        season_status.append(f"{status} **{cat.replace('_', ' ').title()}:** {pred}")
    race_status = []
    for race, preds in race_preds.items():
        race_status.append(f"\n**{race}**")
        for cat, pred in preds.items():
            pred_lower = pred.lower()
            if "podium" in cat:
                drivers = [d.strip().lower() for d in re.findall(r"\d+\.\s*(.+?)\s+(?=\d+\.|$)", pred)]
                valid = all(d in valid_drivers for d in drivers)
            elif cat == "constructors_champion":
                valid = pred_lower in valid_teams
            else:
                valid = pred_lower in valid_drivers
            status = "✅" if valid else "❌"
            race_status.append(f"{status} {cat.replace('_', ' ').title()}: {pred}")
    if season_status:
        embed.add_field(name="Season Predictions", value="\n".join(season_status), inline=False)
    if race_status:
        embed.add_field(name="Race Predictions", value="\n".join(race_status), inline=False)
    if not season_status and not race_status:
        embed.description = "No predictions to validate"
    await ctx.send(embed=embed)

# Run the bot
bot.run('MTMzNTk0Nzk4NjI3MzgyODkzNA.GCl4mv.X9Ta9bheKjiFsaKL5MoZ7SSsKUbhBPn6orZa2M')
