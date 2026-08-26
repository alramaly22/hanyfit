"""Meal recommendation engine.

Single place responsible for turning "a goal + optional targets + optional
exclusions" into a ranked list of Meal objects, so this logic lives in one
service instead of scattered across views/templates (per the brief, section
12). Both the Calorie Calculator (services/calculator.py) and the
"Recommended For You" surface (recommended_for_subscription, used by
meals/views.py on the subscription status page) call this rather than
building their own filtering.

Allergy/disliked-food exclusion is deliberately a simple case-insensitive
keyword match against Meal.name + Meal.ingredients -- there is no structured
ingredient/allergen database in this project to match against instead. This
is a best-effort filter for convenience, not a medical safety guarantee:
templates that show these recommendations should keep saying so.
"""

from ..models import Meal


def _exclusion_keywords(*texts):
    keywords = set()
    for text in texts:
        if not text:
            continue
        for word in text.replace(",", " ").split():
            word = word.strip().lower()
            if len(word) >= 3:  # skip stray "a", "of", ... noise
                keywords.add(word)
    return keywords


def recommend_meals(
    goal=None,
    *,
    meal_type=None,
    calories_target=None,
    protein_target=None,
    allergies_text="",
    disliked_foods_text="",
    limit=6,
):
    """Active meals, optionally narrowed by goal/meal type, with any
    allergy/disliked-food keyword matches excluded, ranked by closeness to
    ``calories_target``/``protein_target`` (treated as a *daily* target and
    compared against a rough one-third-per-meal share) when given.
    """
    meals = Meal.objects.active()
    if goal:
        meals = meals.for_goal(goal)
    if meal_type:
        meals = meals.for_meal_type(meal_type)

    meals = list(meals)

    keywords = _exclusion_keywords(allergies_text, disliked_foods_text)
    if keywords:
        def is_excluded(meal):
            haystack = f"{meal.name} {meal.ingredients}".lower()
            return any(keyword in haystack for keyword in keywords)

        meals = [meal for meal in meals if not is_excluded(meal)]

    if calories_target or protein_target:
        def distance(meal):
            score = 0.0
            if calories_target:
                score += abs(meal.calories - float(calories_target) / 3) / 10
            if protein_target:
                score += abs(float(meal.protein_g) - float(protein_target) / 3)
            return score

        meals.sort(key=distance)

    return meals[:limit]


def recommended_for_subscription(subscription, limit=6):
    """Reads targets straight off a Subscription's own stored preferences
    (goal, allergies, disliked foods) -- the "Recommended For You" idea from
    the brief, minus any Coaching-system integration (none exists in this
    project; see the audit note in the chat)."""
    return recommend_meals(
        goal=subscription.goal,
        allergies_text=subscription.allergies,
        disliked_foods_text=subscription.disliked_foods,
        limit=limit,
    )
