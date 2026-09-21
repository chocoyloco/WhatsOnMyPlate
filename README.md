# Welcome to WhatsOnMyPlate! 

## What is WhatsOnMyPlate? 
It's a tool built with the college student in mind that will help students build a meal plan that closely aligns with their goals!
## How it works
1. **Scrape.** A Python script (`scrape.py`) pulls each dining hall's menu from their respective college's public menu data.
2. **Clean.** It removes section headers and restricted stations, and keeps allergen and dietary tags. This helps eliminate food choices out of students reach!
3. **Automate.** GitHub Actions runs the script every day at 8 AM Pacific and commits the results as JSON.
4. **Display.** A static site on GitHub Pages reads the latest data and lets you filter by hall, meal, and protein picks.


## Tech stack

Python (requests), GitHub Actions, GitHub Pages, HTML/CSS/JavaScript

## Roadmap

- Estimated calories and protein for each dish
- Plate recommendations optimized for personal macro targets (linear programming)
- Build-your-own plate suggestions across stations
- 
## Disclaimer
Not affiliated with USC Hospitality. Allergen information comes from USC's published menus. Always confirm with dining staff if you have a food allergy.
