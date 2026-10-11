// Well-known, general categories for the challenge picker. The question itself
// is written by the server from the chosen category and difficulty.
export const CATEGORIES = [
  'Sports', 'Food', 'Technology', 'Music', 'Movies', 'Video Games', 'Social Media', 'School',
  'Space', 'Animals', 'Travel', 'Health', 'Money', 'Science', 'Art', 'Fashion', 'History',
  'Cars', 'Books', 'Jobs', 'Holidays', 'Cities', 'Oceans', 'Robots', 'Superheroes', 'Pets',
  'Fast Food', 'Smartphones', 'The Olympics', 'Dinosaurs', 'Coffee', 'The Internet',
  'Friendship', 'Theme Parks', 'Television', 'Sleep', 'Chess', 'Weather',
];
export const DIFFICULTIES = ['beginner', 'intermediate', 'expert'];

/** n different categories, in random order. */
export function pickCategories(n = 5, random = Math.random) {
  const pool = [...CATEGORIES];
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  return pool.slice(0, n);
}
