import discord
from discord import app_commands  # Dodaj na początku pliku
from discord.ext import commands, tasks
from discord.ui import Button, View, Select
from replit import db
from PIL import Image
import pytesseract
import io
import os
from discord.ext import commands
import gspread
import asyncio
from oauth2client.service_account import ServiceAccountCredentials
import datetime

# Konfiguracja bota Discord
TOKEN = os.getenv("DISCORD_TOKEN")  # Token bota z ustawień środowiskowych
GUILD_ROLE_ID = 1345759205897801819  # ID roli "Guild"
MANAGEMENT_ROLE_ID = 1345758902842560543  # ID roli "Management"
EVENT_CHANNEL_ID = 1345835157025849474  # ID kanału do wydarzeń
SCREENS_CHANNEL_ID = 1346161292284661871  # ID kanału do screenów
# Konfiguracja Google Sheets
SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
CREDENTIALS_FILE = "credentials.json"  # Plik JSON w głównym katalogu projektu
SPREADSHEET_ID = "1xUMt1p-RHVbtetmL0IrDcU-VmVXtqNZK7wOjNZI13QI"
# Połączenie z Google Sheets
creds = ServiceAccountCredentials.from_json_keyfile_name(CREDENTIALS_FILE, SCOPES)
client = gspread.authorize(creds)
sheet = client.open_by_key(SPREADSHEET_ID).sheet1
dkp_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("DKP")
# Konfiguracja klienta Discord
intents = discord.Intents.all()
intents.messages = True
intents.dm_messages = True  # Pozwala na odbieranie prywatnych wiadomości
bot = commands.Bot(command_prefix="/", intents=intents)
tree = bot.tree  # Tworzymy "drzewo" komend aplikacji (slash)
guild = None  # Tymczasowe przypisanie, później ustawiane w funkcjach
active_event = None  # 🛠️ Upewniamy się, że zmienna jest zainicjalizowana
  #___________________________________________________________
@bot.event
async def on_ready():
    global guild, completed_users
    guild = bot.guilds[0]  # Pobiera pierwszy serwer, na którym jest bot
    print(f'Zalogowano jako {bot.user}')
    try:
        synced = await bot.tree.sync()  # Rejestracja slash komend
        print(f"Zsynchronizowano {len(synced)} komend.")
    except Exception as e:
        print(f"Błąd synchronizacji komend: {e}")
    # Load completed_users from the database
    completed_users = db.get("completed_users", set()) 
    # Register persistent view to handle "Kliknij mnie" button clicks
    try:
        bot.add_view(ItemSelectionView())
        print("Registered persistent ItemSelectionView")
    except Exception as e:
        print(f"Error registering ItemSelectionView: {e}")
        import traceback
        print(traceback.format_exc())
    # Set up the screenshots channel handler
    screenshots_channel = bot.get_channel(1346161292284661871)
    if screenshots_channel:
        print(f"Found screenshots channel: {screenshots_channel.name}")
    else:
        print("Screenshots channel not found. Please check the channel ID.")
    # Set up the item selection button

#___________________________________________________________

# Obsługa screenów i OCR
@bot.event
async def on_message(message):
    # Pomijaj wiadomości od botów
    if message.author.bot:
        return

    # Sprawdź czy wiadomość jest na kanale screenów
    if message.channel.id == SCREENS_CHANNEL_ID:
        # Sprawdź czy wiadomość zawiera załączniki (obrazy)
        if message.attachments:
            has_management_role = any(role.id == MANAGEMENT_ROLE_ID for role in message.author.roles)
            if not has_management_role:
                await message.channel.send(f"{message.author.mention} Only Management can add screenshots!")
                return
            await message.add_reaction("⏳")  # Dodaj reakcję, że przetwarzanie się rozpoczęło

            try:
                screen_results = []

                # Przetwarzanie wszystkich załączników
                for attachment in message.attachments:
                    if any(attachment.filename.lower().endswith(ext) for ext in ['.png', '.jpg', '.jpeg']):
                        # Pobierz obraz
                        image_data = await attachment.read()
                        image = Image.open(io.BytesIO(image_data))

                        # Przygotuj obraz do OCR (poprawa kontrastu, itp.)
                        # Konwertuj do skali szarości
                        image = image.convert('L')

                        # Opcjonalnie można zastosować filtr dla poprawy ostrości
                        from PIL import ImageFilter, ImageEnhance
                        # Zwiększ kontrast
                        enhancer = ImageEnhance.Contrast(image)
                        image = enhancer.enhance(2.0)  # Zwiększ kontrast 2x

                        # Zwiększ ostrość
                        image = image.filter(ImageFilter.SHARPEN)

                        # Użyj OCR do wyodrębnienia tekstu z poprawionym predefiniowanym konfiguracjami
                        custom_config = r'--oem 3 --psm 6 -l pol+eng'  # Konfiguracja dla polskiego i angielskiego tekstu
                        text = pytesseract.image_to_string(image, config=custom_config)

                        # Zapisz debug informacje
                        print(f"Text from the screen was read: {text[:100]}...")  # Pokaż pierwsze 100 znaków

                        # Przetwórz wyniki OCR
                        lines = text.strip().split('\n')
                        for line in lines:
                            if '|' in line and len(line.strip()) > 5:  # Upewnij się, że linia zawiera dane
                                screen_results.append(line.strip())

                if not screen_results:
                    await message.remove_reaction("⏳", bot.user)
                    await message.add_reaction("❌")
                    await message.channel.send(f"{message.author.mention} Could not read data from images. Make sure screenshots contain proper data.")
                    return

                # Wyświetl odczytane dane w konsoli dla debugowania
                print(f"Data read from screenshots ({len(screen_results)} results):")
                for idx, result in enumerate(screen_results):
                    print(f"{idx+1}. {result}")

                # Teraz zapytaj o informacje o wydarzeniu
                event_info = await ask_for_event_info(message)

                if not event_info:
                    await message.remove_reaction("⏳", bot.user)
                    await message.add_reaction("❌")
                    return

                # Zapisz dane do arkusza
                await save_screen_data(message, event_info, screen_results)

                # Oznacz sukces
                await message.remove_reaction("⏳", bot.user)
                await message.add_reaction("✅")

            except Exception as e:
                print(f"Error processing screenshots: {e}")
                import traceback
                print(traceback.format_exc())
                await message.remove_reaction("⏳", bot.user)
                await message.add_reaction("❌")
                await message.channel.send(f"{message.author.mention} An error occurred while processing images: {str(e)}")

    # Zapewnij, że eventy komend są również obsługiwane
    await bot.process_commands(message)
async def ask_for_event_info(message):
    """Pytanie o informacje dotyczące wydarzenia"""
    try:
        # Pytanie o wydarzenie
        events_options = list(events.keys())

        event_name_prompt = await message.channel.send(
            f"{message.author.mention} Select an event:"
        )

        event_select = Select(placeholder="Select event type...", options=[
            discord.SelectOption(label=event_name, value=event_name) for event_name in events_options
        ])

        event_view = View(timeout=120)
        event_view.add_item(event_select)
        event_info = {}  # Inicjalizacja słownika z informacjami o wydarzeniu
        # Obsługa wyboru wydarzenia
        async def event_selection_callback(interaction):
            if interaction.user != message.author:
                await interaction.response.send_message("This is not your query!", ephemeral=True)
                return

            event_info["name"] = interaction.data['values'][0]
            await interaction.response.send_message(f"Selected: {event_info['name']}", ephemeral=True)

            # Pytanie o datę
            date_prompt = await message.channel.send(
                f"{message.author.mention} Select event date:"
            )

            date_options = [
                discord.SelectOption(label="Today", value="today"),
                discord.SelectOption(label="Other date", value="other")
            ]

            date_select = Select(placeholder="Select date...", options=date_options)
            date_view = View(timeout=120)
            date_view.add_item(date_select)

            # Obsługa wyboru daty
            async def date_selection_callback(interaction):
                if interaction.user != message.author:
                    await interaction.response.send_message("This is not your query!", ephemeral=True)
                    return

                date_choice = interaction.data['values'][0]

                if date_choice == "today":
                    event_info["date"] = datetime.datetime.now().strftime("%d.%m.%Y")
                    await interaction.response.send_message(f"Date selected: {event_info['date']}", ephemeral=True)

                    # Przejdź do wyboru godziny
                    await ask_for_time()

                else:  # "other"
                    await interaction.response.send_message("Enter the date in the format DD.MM.YYYY:", ephemeral=True)

                    def check_msg(m):
                        return m.author == message.author and m.channel == message.channel

                    try:
                        date_msg = await bot.wait_for('message', check=check_msg, timeout=60)
                        event_info["date"] = date_msg.content.strip()
                        await date_msg.delete()

                        # Przejdź do wyboru godziny
                        await ask_for_time()

                    except asyncio.TimeoutError:
                        await message.channel.send("⏱️ The time to enter a date has expired.")
                        return None

            date_select.callback = date_selection_callback
            await message.channel.send(view=date_view)

            # Funkcja do pytania o godzinę
            async def ask_for_time():
                time_prompt = await message.channel.send(
                    f"{message.author.mention} Select event time:"
                )

                # Generowanie opcji godzin od 16:00 do 01:00 co 30 minut
                time_options = []
                for hour in range(16, 24 + 1):  # Od 16 do 24
                    actual_hour = hour if hour <= 24 else hour - 24
                    time_options.append(discord.SelectOption(label=f"{actual_hour:02d}:00", value=f"{actual_hour:02d}:00"))
                    time_options.append(discord.SelectOption(label=f"{actual_hour:02d}:30", value=f"{actual_hour:02d}:30"))

                # Dodaj 01:00
                time_options.append(discord.SelectOption(label="01:00", value="01:00"))

                time_select = Select(placeholder="Select a time...", options=time_options)
                time_view = View(timeout=120)
                time_view.add_item(time_select)

                # Obsługa wyboru godziny
                async def time_selection_callback(interaction):
                    if interaction.user != message.author:
                        await interaction.response.send_message("This is not your query!", ephemeral=True)
                        return

                    event_info["time"] = interaction.data['values'][0]
                    await interaction.response.send_message(f"Selected time: {event_info['time']}", ephemeral=True)

                    # Podsumowanie wyborów
                    summary = f"📝 **Event Summary:**\n"\
                              f"🎯 Event: **{event_info['name']}**\n"\
                              f"📅 Date: **{event_info['date']}**\n"\
                              f"⏰ Time: **{event_info['time']}**\n\n"\
                              f"Is everything correct?"

                    confirm_view = View(timeout=120)
                    confirm_button = Button(label="Yes, Save", style=discord.ButtonStyle.green)
                    cancel_button = Button(label="Cancel", style=discord.ButtonStyle.red)

                    async def confirm_callback(interaction):
                        if interaction.user != message.author:
                            await interaction.response.send_message("This is not your query!", ephemeral=True)
                            return

                        await interaction.response.send_message("✅ I am saving data...", ephemeral=True)
                        # Dane są już w event_info, więc możemy zakończyć funkcję
                        confirm_view.stop()

                    async def cancel_callback(interaction):
                        if interaction.user != message.author:
                            await interaction.response.send_message("This is not your query!", ephemeral=True)
                            return

                        await interaction.response.send_message("❌ Data saving canceled.", ephemeral=True)
                        event_info.clear()  # Czyszczenie informacji
                        confirm_view.stop()

                    confirm_button.callback = confirm_callback
                    cancel_button.callback = cancel_callback

                    confirm_view.add_item(confirm_button)
                    confirm_view.add_item(cancel_button)

                    await message.channel.send(summary, view=confirm_view)

                time_select.callback = time_selection_callback
                await message.channel.send(view=time_view)

        event_select.callback = event_selection_callback
        await message.channel.send(view=event_view)

        # Czekamy na zakończenie procesu wyboru
        await asyncio.sleep(120)  # Maksymalny czas oczekiwania

        if event_info and all(key in event_info for key in ["name", "date", "time"]):
            return event_info
        else:
            return None

    except Exception as e:
        print(f"Error in ask_for_event_info: {e}")
        import traceback
        print(traceback.format_exc())
        return None
async def save_screen_data(message, event_info, screen_results):
    """Zapisuje dane z OCR do arkusza Google Sheets"""
    try:
        # Znajdź arkusz "Screens" lub utwórz go, jeśli nie istnieje
        try:
            screens_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("Screens")
        except gspread.exceptions.WorksheetNotFound:
            # Jeśli arkusz nie istnieje, utwórz go
            sheet = client.open_by_key(SPREADSHEET_ID)
            screens_sheet = sheet.add_worksheet(title="Screens", rows=1000, cols=20)
            # Dodaj nagłówki
            screens_sheet.update_cell(1, 1, "Data")
            screens_sheet.update_cell(1, 2, "Godzina")
            screens_sheet.update_cell(1, 3, "Wydarzenie")
            screens_sheet.update_cell(1, 4, "Informacja")

        # Zapisz każdy wynik w osobnym wierszu
        for result in screen_results:
            screens_sheet.append_row([
                event_info["date"],
                event_info["time"],
                event_info["name"],
                result
            ])
        # Usuń wszystkie wiadomości bota w kanale
        def is_bot_message(msg):
            return msg.author == bot.user

        await message.channel.purge(limit=100, check=is_bot_message)
        
        # Po zapisaniu danych do arkusza Google Sheets
        await message.channel.send(f"✅ Saved {len(screen_results)} results in Google Sheets.")

    except Exception as e:
        print(f"Error writing data to Sheets: {e}")
        import traceback
        print(traceback.format_exc())
        await message.channel.send(f"❌ An error occurred while saving data: {str(e)}")
#___________________________________________________________
@bot.tree.command(name="reset_items", description="Reset saved information about the specified user's needed items")
async def reset_items(interaction: discord.Interaction, user: discord.User):
    has_management_role = any(role.id == MANAGEMENT_ROLE_ID for role in interaction.user.roles)
    if not has_management_role:
        await interaction.response.send_message("❌ You do not have permission to reset your items.", ephemeral=True)
        return
    # Check if the user exists in user_answers and completed_users
    if str(user.id) not in db:
        await interaction.response.send_message("⚠️ This user has no data saved.", ephemeral=True)
        return
    # Remove user's saved data
    del db[str(user.id)]
    completed_users.discard(user.id)  # Remove from completed_users if present
    # Clear data from the "BIS ITEMS" Google Sheets for that specific user
    try:
        bis_items_sheet = client.open_by_key(SPREADSHEET_ID).worksheet("BiS ITEMS")  # Updated to match exact case
        all_records = bis_items_sheet.get_all_values()
        rows_to_delete = []
        # Identify rows to delete for the specified user
        for i, record in enumerate(all_records):
            if str(user.id) in record:  # Check if the user ID is in the record
                rows_to_delete.append(i + 1)  # Collect row numbers (1-indexed for Google Sheets)
        # Delete the rows in reverse order to avoid shifting issues
        for row in reversed(rows_to_delete):
            bis_items_sheet.delete_rows(row)
        await interaction.response.send_message(f"✅ Reset save for user {user.name}.", ephemeral=True)
    except gspread.exceptions.WorksheetNotFound:
        await interaction.response.send_message("⚠️ Sheet 'BIS ITEMS' not found.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"⚠️ Error while resetting data: {e}", ephemeral=True)


# Lista wydarzeń
events = {
    "Tax Delivery": 5,
    "Siege": 10,
    "Boonstone GvG": 8,
    "Riftstone GvG": 8,
    "Wargames": 6,
    "World Boss": 7,
    "Archboss": 9,
    "Night PVP": 4
}
# Przechowuje zapisanych użytkowników
active_event = None
participants = set()
@bot.event
async def on_ready():
    global guild
    guild = bot.guilds[0]  # Pobiera pierwszy serwer, na którym jest bot
    print(f'Zalogowano jako {bot.user}')
    try:
        synced = await bot.tree.sync()  # Rejestracja slash komend
        print(f"Zsynchronizowano {len(synced)} komend.")
    except Exception as e:
        print(f"Błąd synchronizacji komend: {e}")
    # Register persistent view to handle "Kliknij mnie" button clicks
    try:
        bot.add_view(ItemSelectionView())
        print("Registered persistent ItemSelectionView")
    except Exception as e:
        print(f"Error registering ItemSelectionView: {e}")
        import traceback
        print(traceback.format_exc())
    # Set up the screenshots channel handler
    screenshots_channel = bot.get_channel(1346161292284661871)
    if screenshots_channel:
        print(f"Found screenshots channel: {screenshots_channel.name}")
    else:
        print("Screenshots channel not found. Please check the channel ID.")
    # Set up the item selection button


@bot.tree.command(name="event", description="Rozpocznij wydarzenie DKP")
async def event(interaction: discord.Interaction):
    global active_event, participants  
    has_management_role = any(role.id == MANAGEMENT_ROLE_ID for role in interaction.user.roles)
    if not has_management_role:
        await interaction.response.send_message("❌ Only Management can make event.", ephemeral=True)
        return
    if active_event:
        await interaction.response.send_message("❌ There is another event going on right now", ephemeral=True)
        return
    await interaction.response.send_message("📢 Choose an event!", ephemeral=True)
    class EventSelect(Select):
        def __init__(self):
            options = [discord.SelectOption(label=event, value=event) for event in events.keys()]
            super().__init__(placeholder="Choose an event...", options=options)
        async def callback(self, interaction: discord.Interaction):
            global active_event, participants  
            event_name = self.values[0]  # Pobranie nazwy wybranego wydarzenia
            dkp_points = events[event_name]  
            active_event = event_name  # Przypisanie aktywnego wydarzenia
            participants = set()
            # Pytanie o godzinę startu
            hour_select = Select(placeholder="Select start time", options=[
                discord.SelectOption(label=f"{hour:02d}:{minute:02d}", value=f"{hour:02d}:{minute:02d}")
                for hour in range(16, 24 + 1)
                for minute in [0, 30]
            ] + [discord.SelectOption(label="00:00", value="00:00")]
            + [discord.SelectOption(label="00:30", value="00:30")]
            + [discord.SelectOption(label="01:00", value="01:00")]
            )

            hour_view = View()
            hour_view.add_item(hour_select)
            await interaction.response.send_message("Select start time:", view=hour_view, ephemeral=True)
            async def hour_selection_callback(interaction: discord.Interaction):
                event_info = {"name": event_name, "time": hour_select.values[0]}  
                await interaction.response.send_message(f"Selected time: {event_info['time']}", ephemeral=True)
                # Proceed with the rest of the event setup
                button = Button(label="sing up", style=discord.ButtonStyle.green)
                async def button_callback(interaction):
                    if interaction.user.id not in participants:
                        participants.add(interaction.user.id)
                        await interaction.response.send_message("✅ You have been registered for the event!", ephemeral=True)
                    else:
                        await interaction.response.send_message("⚠️ You are already registered!", ephemeral=True)
                button.callback = button_callback
                view = View()
                view.add_item(button)
                event_channel = bot.get_channel(EVENT_CHANNEL_ID)
                guild_role = interaction.guild.get_role(GUILD_ROLE_ID)
                message = await event_channel.send(
                    f"🎉 **{event_name}** started! Sign up for the event below."
                )
                event_message = await event_channel.send(f"{guild_role.mention} you have **10 minutes** to sign up!", view=view)
                await asyncio.sleep(60)  # Czekaj 10 minut
                # Dezaktywuj przycisk - zastąp wiadomość z przyciskiem wersją bez aktywnego przycisku
                await event_message.edit(content=f"⏱️ Registrations have been closed!", view=None)
                await finalize_event(event_name, dkp_points, message, event_info["time"])  # Pass the time
            hour_select.callback = hour_selection_callback
    view = View()
    select = EventSelect()
    view.add_item(select)
    await interaction.followup.send("Select an event:", view=view, ephemeral=True)

import datetime
async def finalize_event(event_name, dkp_points, message, event_time):
    """Zamyka wydarzenie i zapisuje dane do Google Sheets"""
    global active_event, participants
    print(f"Zamykam zapisy dla wydarzenia: {event_name}")
    if not participants:
        await message.edit(content=f"📢 **{event_name}** is closed, but no one signed up. ❌", view=None)
        active_event = None
        participants.clear()
        return  # Wyjdź z funkcji, jeśli nikt się nie zapisał
    event_channel = bot.get_channel(EVENT_CHANNEL_ID)
    await event_channel.send("🔒 Registration is closed! I'm awarding DKP points...")
    try:
        # Pobranie aktualnej daty i godziny
        current_date = datetime.datetime.now().strftime("%d.%m.%Y")
        # Pobranie numeru ostatniego wydarzenia z arkusza
        all_records = dkp_sheet.get_all_values()
        # Pobranie ostatniego numeru wydarzenia (sprawdzając pierwszą kolumnę w arkuszu)
        event_numbers = [int(row[0]) for row in all_records[1:] if row and row[0].isdigit()]
        event_number = max(event_numbers, default=0) + 1  # Numer ostatniego wydarzenia + 1
        # Pobranie serwera
        guild = bot.guilds[0]
        for user_id in participants:
            user = bot.get_user(user_id) or await bot.fetch_user(user_id)
            if user is None:
                print(f"❌ User with ID not found {user_id} on serwer.")
                continue
            # Zapisujemy dane w formacie: Numer wydarzenia | Data | Nazwa użytkownika | ID użytkownika | Nazwa wydarzenia | DKP
            dkp_sheet.append_row([event_number, current_date, event_time, user.name, str(user.id), event_name, str(dkp_points)])
            try:
                await user.send(f"✅ Your presence on **{event_name}** has been saved. ")
            except discord.Forbidden:
                print(f"⚠️ Failed to send message to  {user.name} (has DM blocked).")
        await event_channel.send(f"✅ **{event_name}** finished! Results saved.")
        active_event = None
        participants.clear()
    except Exception as e:
        print(f"❌ DKP save error: {e}")

# Kategorie przedmiotów i odpowiedzi
categories = [
    "Archboss Weapons",
    "Archboss Accessories"
]
options = {
    "Archboss Weapons": [
        "Tevent's Arc of Wailing Death",
        "Tevent's Fangs of Fury",
        "Tevent's Warblade of Despair",
        "Tevent's Grasp of Withering",
        "Queen Bellandir's Hivemind Staff",
        "Queen Bellandir's Languishing Blade",
        "Queen Bellandirs's Serrated Spike",
        "Queen Bellandir's Toxic Spine Throwers",
        "Deluzhnoa's Hail Spear",
        "Deluzhnoa's Ice Wood",
        "Deluzhnoa's Frost Bow",
        "Deluzhnoa's Ice Slash",
        "Deluzhnoa's Ice Sword",
        "Giant Cordy's Domination Bow",
        "Giant Cordy's Spore Hand",
        "Giant Cordy's Corruption Sword",
        "NONE"
    ],
    "Archboss Accessories": [
        "Ice Queen's Bracelet",
        "Stone Cold Heart Cloak",
        "Fell Tree Belt",
        "Great Tree Sap Necklace",
        "Forest Ruler's Bracelet",
        "Tree King's Ring",
        "Golden Crown of Frigid Frost",
        "Cold Snap's Ice Ring",
        "NONE"
    ]
}
bis_items = []
user_answers = {}
@bot.event
async def on_close():
    db["completed_users"] = completed_users= set()

completed_users = set()  # Zestaw użytkowników, którzy już wypełnili formularz
async def ask_question(user, index):
    """Zadaje pytania użytkownikowi w prywatnej wiadomości"""
    if index >= len(categories):
        await save_answers(user)
        return
    category = categories[index]
    options_list = [
        discord.SelectOption(label=option, value=option) for option in options[category]
    ]
    select = Select(placeholder=f"Choose{category}", options=options_list)
    async def select_callback(interaction: discord.Interaction):
        if str(user.id) in db and "completed" in db[str(user.id)]:
            await interaction.response.send_message("❌ You have already submitted your answers. You cannot edit them!", ephemeral=True)
            return
        if str(user.id) not in db:
            db[str(user.id)] = {}  # Inicjalizuj pusty słownik dla użytkownika
        db[str(user.id)][category] = interaction.data['values'][0]
        await interaction.response.send_message(f"✅ Choose {interaction.data['values'][0]}", ephemeral=True)
        await ask_question(user, index + 1)
    select.callback = select_callback
    view = View()
    view.add_item(select)
    await user.send(f"📌 Choose {category}:", view=view)
async def save_answers(user):
    """Zapisuje odpowiedzi użytkownika do Google Sheets i blokuje ponowne zmiany"""
    try:
        answers = db.get(str(user.id), {})
        if len(answers) == len(categories):  
            user_data = [user.name, str(user.id)] + [answers[cat] for cat in categories]
            sheet.append_row(user_data)  # Use the global sheet variable instead of bis_items.sheet
            db[str(user.id)]["completed"] = True  # Dodajemy użytkownika do listy zakończonych
            await user.send("✅ Thank you! Your answers have been saved and **you can no longer change them.**")
            print(f"Odpowiedzi użytkownika {user.name} zapisane w Google Sheets.")
        else:
            await user.send("⚠️ We did not save your responses because you did not complete the entire form.")
    except Exception as e:
        print(f"❌ Failed to save response: {e}")
        await user.send("⚠️ There was an error saving your answers. Please try again later.")
# Define the persistent view class for item selection
class ItemSelectionView(discord.ui.View):
    def __init__(self):
        # Setting timeout to None makes the view persistent
        super().__init__(timeout=None)

    @discord.ui.button(label="Choose your BiS items", style=discord.ButtonStyle.green, custom_id="select_items_button")
    async def select_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Access the user object from the interaction
        user = interaction.user
        # ... (rest of your code)
        if str(user.id) in db and "completed" in db[str(user.id)]:
            await interaction.response.send_message("❌ You have already filled out the form once. If you want to change your selections, pls contact to menagements.", ephemeral=True)
            return
        # Load user answers from the database
        user_answers = db.get(str(user.id), {})  # Load from db

        # Send acknowledgment
        await interaction.response.send_message("✅ I am sending questions in a private message..", ephemeral=True)

        try:
            # Start the questionnaire process
            await ask_question(user, 0)
            print(f"Started questionnaire for user {user.name}")
        except Exception as e:
            print(f"Error sending DM to {user.name}: {e}")
            await interaction.followup.send("❌ There was an error sending your questions. Please make sure you have private messaging enabled.", ephemeral=True)
    # Function to set up the item selection button
@bot.event
async def on_guild_join(guild):
    channel = bot.get_channel(1345776049648177232)
    if channel is not None:
        embed = discord.Embed(
            title="BIS Subject Selection",
            description="👋 Welcome! Click the button below to select item categories.",
            color=discord.Color.blue()
        )
        embed.set_footer(text="By clicking the button you will receive a private message with your questions.")
        await channel.send(embed=embed, view=ItemSelectionView())
        
bot.run(TOKEN)
