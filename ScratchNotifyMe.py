import discord
from discord import app_commands
from discord.ext import commands, tasks
import requests
from pymongo import MongoClient

#Connect to MongoDB
client = MongoClient("MONGODB_URI")
db = client['ScratchNotifyMe']
users_collection = db['tracked_users']

#Define the bot
intents = discord.Intents.default()
bot = commands.Bot(command_prefix='', intents=intents)

#Get a user's projects from the Scratch API
def get_user_projects(username):
    url = f"https://api.scratch.mit.edu/users/{username}/projects/"
    response = requests.get(url)
    if response.status_code == 200:
        return response.json()
    else:
        print(f"Error fetching projects for user {username}. Status code: {response.status_code}")
        return []

#Fetch and initialize known projects for a new user
def initialize_known_projects(username):
    projects = get_user_projects(username)
    project_ids = [project['id'] for project in projects]
    return project_ids

#Slash command to add a Scratch user in this channel or DM
@bot.tree.command(name="adduser", description="Add a Scratch user to track in this channel or DM")
@app_commands.describe(username="The Scratch username to add")
async def add_user(interaction: discord.Interaction, username: str):
    #Check if the channel/DM is a DM or a channel
    if isinstance(interaction.channel, discord.DMChannel):
        identifier = str(interaction.user.id)
        is_dm = True
    else:
        identifier = str(interaction.channel.id)
        is_dm = False

    #Check if the user is already being tracked
    user_exists = users_collection.find_one({"identifier": identifier, "username": username})

    if user_exists:
        await interaction.response.send_message(f"**{username}** is already being tracked in this channel.")
    else:
        known_projects = initialize_known_projects(username)
        
        #Add user to MongoDB
        users_collection.insert_one({
            "identifier": identifier,
            "username": username,
            "is_dm": is_dm,
            "known_projects": known_projects
        })
        
        await interaction.response.send_message(f"Started tracking **{username}**'s projects in this channel.")

#Slash command to remove a Scratch user for the current channel or DM
@bot.tree.command(name="deluser", description="Remove a Scratch user from this channel or DM's tracking list")
@app_commands.describe(username="The Scratch username to remove")
async def del_user(interaction: discord.Interaction, username: str):
    if isinstance(interaction.channel, discord.DMChannel):
        identifier = str(interaction.user.id)
    else:
        identifier = str(interaction.channel.id)

    result = users_collection.delete_one({"identifier": identifier, "username": username})

    if result.deleted_count > 0:
        await interaction.response.send_message(f"Stopped tracking **{username}**'s projects in this channel/DM.")
    else:
        await interaction.response.send_message(f"**{username}** is not being tracked in this channel/DM.")

#Slash command to view the tracked users in the current channel or DM
@bot.tree.command(name="viewusers", description="View the Scratch users being tracked in this channel or DM")
async def view_users(interaction: discord.Interaction):
    #Check if it's a DM or a server channel
    if isinstance(interaction.channel, discord.DMChannel):
        identifier = str(interaction.user.id)  #Use user ID for DMs
    else:
        identifier = str(interaction.channel.id)  #Use channel ID for server channels
    
    tracked_users = list(users_collection.find({"identifier": identifier}))
    
    if len(tracked_users) == 0:
        await interaction.response.send_message("No Scratch users are being tracked in this channel/DM.")
    else:
        user_list = [f"**{user_entry['username']}**" for user_entry in tracked_users]
        await interaction.response.send_message(f"Tracked users in this channel/DM:\n" + "\n".join(user_list))

async def notify_new_project(identifier, username, project, is_dm):
    if is_dm:
        user = await bot.fetch_user(int(identifier))
        await user.send(f"New project by **{username}**!\nTitle: {project['title']}\nLink: https://scratch.mit.edu/projects/{project['id']}/")
    else:
        channel = bot.get_channel(int(identifier))
        if channel:
            await channel.send(f"New project by **{username}**!\nTitle: {project['title']}\nLink: https://scratch.mit.edu/projects/{project['id']}/")

@tasks.loop(seconds=60)
async def track_new_projects():
    for identifier in users_collection.distinct("identifier"):
        users = users_collection.find({"identifier": identifier})
        for user_entry in users:
            username = user_entry['username']
            is_dm = user_entry.get('is_dm', False)
            current_projects = get_user_projects(username)
            current_project_ids = {project['id'] for project in current_projects}

            known_projects = set(user_entry.get('known_projects', []))

            #Remove projects that are no longer shared
            removed_projects = known_projects - current_project_ids
            if removed_projects:
                users_collection.update_one(
                    {"identifier": identifier, "username": username},
                    {"$pull": {"known_projects": {"$in": list(removed_projects)}}}
                )
            
            #Check for new projects
            new_projects = current_project_ids - known_projects
            for project_id in new_projects:
                project = next(project for project in current_projects if project['id'] == project_id)
                await notify_new_project(identifier, username, project, is_dm)

                #Update database
                users_collection.update_one(
                    {"identifier": identifier, "username": username},
                    {"$addToSet": {"known_projects": project_id}}
                )

@bot.event
async def on_ready():
    await bot.tree.sync()  #Sync commands with Discord
    track_new_projects.start()  #Start  background task to track new projects
    print(f"Logged in as {bot.user}")

bot.run("DISCORD_BOT_TOKEN")
