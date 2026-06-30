import requests

recipes = [
    {
        "title": "Krispiga Airfryer-pommes",
        "description": "Hemligheten till de perfekta pommesen: blötläggning och rätt temperatur.",
        "category": "TILLBEHÖR",
        "image": "https://images.unsplash.com/photo-1630384060421-cb20d0e0649d?auto=format&fit=crop&q=80&w=800",
        "time": "20m",
        "temp": "200°C",
        "servings": 4,
        "instructions": "1. Skala och skär potatisen i stavar. 2. Blötlägg i kallt vatten i 30 min. 3. Torka noga! 4. Blanda med lite olja och flingsalt. 5. Kör i airfryer i 15-20 min, skaka korgen halvvägs."
    },
    {
        "title": "Honungs- & vitlöksvingar",
        "description": "Klibbiga, söta och med ett sting av vitlök. En riktig crowd-pleaser.",
        "category": "MIDDAGAR",
        "image": "https://images.unsplash.com/photo-1569058242253-92a9c755a0ec?auto=format&fit=crop&q=80&w=800",
        "time": "25m",
        "temp": "200°C",
        "servings": 4,
        "instructions": "1. Torka kycklingvingarna. 2. Pensla med olja. 3. Kör i airfryer i 20 min. 4. Blanda honung, soja och riven vitlök i en panna. 5. Vänd vingarna i glazen och kör 2 min till."
    },
    {
        "title": "Airfryer-chokladmuffins",
        "description": "Saftiga muffins direkt i airfryern. Perfekt när sötsuget sätter in.",
        "category": "EFTERRÄTTER",
        "image": "https://images.unsplash.com/photo-1607920593851-f099c264a66a?auto=format&fit=crop&q=80&w=800",
        "time": "15m",
        "temp": "160°C",
        "servings": 6,
        "instructions": "1. Rör ihop din favoritsmet för chokladmuffins. 2. Häll i små formar. 3. Kör i airfryern på 160 grader i ca 10-12 minuter. 4. Låt svalna och pudra över lite florsocker."
    }
]

# VIKTIGT: Du måste vara inloggad för att lägga till recept!
# Ersätt "user-token-1" med din faktiska token från din databas om den är annorlunda.
headers = {"Authorization": "Bearer user-token-1", "Content-Type": "application/json"}

for r in recipes:
    res = requests.post("http://127.0.0.1:8080/api/recipes", json=r, headers=headers)
    print(f"La till {r['title']}: {res.status_code}")