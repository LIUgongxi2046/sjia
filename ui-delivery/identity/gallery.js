const response = await fetch(new URL('./catalog.json', import.meta.url));
if (!response.ok) throw new Error('形象目录读取失败');
const catalog = await response.json();
const query = new URLSearchParams(location.search);
const selection = { age: 'all', gender: 'all', season: query.get('season') || 'all' };
const entries = catalog.ages.flatMap(age => catalog.genders.flatMap(gender => catalog.seasons.map(season => ({
  id: `${age.id}-${gender.id}-${season.id}`, age, gender, season,
  title: `${age.label} · ${gender.label} · ${season.label}`,
}))));
const path = (item, size = '2x') => new URL(`./${size}/${item.id}${size === 'master' ? '' : '@' + size}.png`, import.meta.url).href;
let active = entries.find(item => item.id === 'toddler-girl-spring');
const grid = document.querySelector('#character-grid');

function choose(item) {
  active = item;
  const image = document.querySelector('#selected-image');
  image.src = path(item);
  image.alt = item.title;
  document.querySelector('#selected-title').textContent = item.title;
  document.querySelector('#selected-clothes').textContent = item.season.clothes;
  document.querySelector('#download-master').href = path(item, 'master');
  document.querySelectorAll('.character-card').forEach(button => button.setAttribute('aria-pressed', String(button.dataset.id === item.id)));
}

function render() {
  const shown = entries.filter(item => Object.entries(selection).every(([key,value]) => value === 'all' || item[key].id === value));
  grid.replaceChildren();
  for (const item of shown) {
    const button = document.createElement('button');
    button.className = 'character-card';
    button.dataset.id = item.id;
    button.style.setProperty('--season', item.season.color);
    button.setAttribute('aria-label', item.title);
    button.setAttribute('aria-pressed', String(item.id === active.id));
    const image = document.createElement('img');
    image.src = path(item);
    image.alt = item.title;
    const season = document.createElement('span');
    season.className = 'season-name';
    season.textContent = item.season.label;
    const title = document.createElement('strong');
    title.textContent = `${item.age.label} · ${item.gender.label}`;
    const detail = document.createElement('small');
    detail.textContent = item.season.clothes;
    button.append(season, image, title, detail);
    button.addEventListener('click', () => choose(item));
    grid.append(button);
  }
  document.querySelector('#result-count').textContent = `当前显示 ${shown.length} 套形象`;
}

for (const [key,label,options] of [['age','年龄',catalog.ages],['gender','性别',catalog.genders],['season','季节',catalog.seasons]]) {
  const group = document.createElement('div');
  group.className = 'filter-group';
  group.setAttribute('role','group');
  group.setAttribute('aria-label',label);
  const title = document.createElement('span');
  title.textContent = label;
  group.append(title);
  for (const option of [{id:'all',label:'全部'},...options]) {
    const button = document.createElement('button');
    button.textContent = option.label;
    button.setAttribute('aria-pressed',String(selection[key] === option.id));
    button.addEventListener('click',() => {
      selection[key] = option.id;
      group.querySelectorAll('button').forEach(control => control.setAttribute('aria-pressed',String(control === button)));
      render();
    });
    group.append(button);
  }
  document.querySelector('#filters').append(group);
}
if (query.has('board')) document.body.classList.add('board-mode');
render();
choose(active);
