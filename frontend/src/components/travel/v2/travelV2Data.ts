import type { DemoTripState, DemoTripStop } from './travelV2State'

export const TRAVEL_PHOTOS = {
  hangzhou: 'https://images.unsplash.com/photo-1530789253388-582c481c54b0?auto=format&fit=crop&w=1400&q=85',
  westLake: 'https://images.unsplash.com/photo-1501785888041-af3ef285b470?auto=format&fit=crop&w=1000&q=85',
  temple: 'https://images.unsplash.com/photo-1545569341-9eb8b30979d9?auto=format&fit=crop&w=900&q=85',
  garden: 'https://images.unsplash.com/photo-1516483638261-f4dbaf036963?auto=format&fit=crop&w=900&q=85',
  city: 'https://images.unsplash.com/photo-1519501025264-65ba15a82390?auto=format&fit=crop&w=1000&q=85',
  coast: 'https://images.unsplash.com/photo-1500375592092-40eb2168fd21?auto=format&fit=crop&w=1000&q=85',
}

export interface TravelTemplate {
  slug: string
  city: string
  title: string
  duration: string
  style: string
  people: number
  budget: string
  description: string
  image: string
  tags: string[]
}

export const TRAVEL_TEMPLATES: TravelTemplate[] = [
  {
    slug: 'hangzhou-slow', city: '杭州', title: '杭州湖山慢游', duration: '3天2晚', style: '自然 · 人文',
    people: 2, budget: '约 ¥2,000/人', description: '从灵隐的清晨走到西湖的傍晚，留足喝茶和散步的时间。',
    image: TRAVEL_PHOTOS.hangzhou, tags: ['经典路线', '节奏轻松'],
  },
  {
    slug: 'suzhou-garden', city: '苏州', title: '苏州园林与水巷', duration: '2天1晚', style: '园林 · 美食',
    people: 2, budget: '约 ¥1,500/人', description: '看一座园林，走一段水巷，尝当季苏式小吃。',
    image: TRAVEL_PHOTOS.garden, tags: ['周末出发', '人文漫步'],
  },
  {
    slug: 'xiamen-sea', city: '厦门', title: '厦门海风假日', duration: '3天2晚', style: '海边 · 美食',
    people: 2, budget: '约 ¥2,300/人', description: '沿海散步、逛老街，在海边留一段不赶时间的午后。',
    image: TRAVEL_PHOTOS.coast, tags: ['海岛假期', '美食路线'],
  },
]

export const TRAVEL_CANDIDATES: DemoTripStop[] = [
  { id: 'candidate-botanical', title: '杭州植物园', time: '15:00', duration: 90, transport: 'walk', category: '景点', image: TRAVEL_PHOTOS.westLake, description: '林荫步道与山茶园，适合慢慢散步。' },
  { id: 'candidate-longjing', title: '龙井茶园', time: '15:30', duration: 90, transport: 'transit', category: '活动', image: TRAVEL_PHOTOS.garden, description: '茶园步道与茶文化体验。' },
  { id: 'candidate-tea', title: '山外山餐厅', time: '12:00', duration: 75, transport: 'drive', category: '餐饮', image: TRAVEL_PHOTOS.temple, description: '杭帮菜，适合作为午间休息点。' },
  { id: 'candidate-break', title: '湖畔咖啡休息', time: '16:00', duration: 60, transport: 'walk', category: '休息', image: TRAVEL_PHOTOS.westLake, description: '留一小时坐下喝茶、看湖景。' },
]

export const INITIAL_DEMO_TRIP: DemoTripState = {
  demoOnly: true,
  hasLocalEdits: false,
  undoItinerary: null,
  undoHistory: [],
  itinerary: {
    destination: '杭州', people: 2, budget: 4000, style: '轻松 · 自然风景、美食',
    days: [
      {
        day: 1, date: '10月18日', stops: [
          { id: 'lingyin', title: '灵隐寺', time: '09:00', duration: 120, transport: 'walk', category: '景点', image: TRAVEL_PHOTOS.temple, description: '沿山林古道游览寺院，上午人相对少，适合慢慢逛。' },
          { id: 'longjing-village', title: '龙井村茶园', time: '12:00', duration: 90, transport: 'transit', category: '餐饮', image: TRAVEL_PHOTOS.garden, description: '在茶园边吃午饭，再沿村中小路散步。' },
          { id: 'west-lake', title: '西湖 · 杨公堤', time: '15:00', duration: 120, transport: 'walk', category: '景点', image: TRAVEL_PHOTOS.westLake, description: '沿湖边步道看水岸和林荫，行程留有休息空间。' },
          { id: 'hefang', title: '河坊街晚餐', time: '18:30', duration: 105, transport: 'drive', category: '餐饮', image: TRAVEL_PHOTOS.city, description: '逛老街并就近用晚餐，按当天客流灵活选择。' },
        ],
      },
      {
        day: 2, date: '10月19日', stops: [
          { id: 'boat', title: '西湖游船', time: '09:30', duration: 90, transport: 'transit', category: '活动', image: TRAVEL_PHOTOS.westLake, description: '乘船看湖心岛与湖岸景色。' },
          { id: 'museum', title: '浙江省博物馆', time: '13:00', duration: 105, transport: 'drive', category: '景点', image: TRAVEL_PHOTOS.temple, description: '室内参观，留出午餐与交通时间。' },
          { id: 'nan-song', title: '南宋御街', time: '16:00', duration: 90, transport: 'transit', category: '景点', image: TRAVEL_PHOTOS.city, description: '傍晚逛街，尝些本地小吃。' },
        ],
      },
      {
        day: 3, date: '10月20日', stops: [
          { id: 'xixi', title: '西溪湿地', time: '09:00', duration: 180, transport: 'transit', category: '景点', image: TRAVEL_PHOTOS.westLake, description: '湿地步道与水上游览，按天气调整停留时间。' },
          { id: 'coffee', title: '湖滨午后咖啡', time: '14:00', duration: 75, transport: 'drive', category: '休息', image: TRAVEL_PHOTOS.garden, description: '留出收尾与休息时间，结束三天行程。' },
        ],
      },
    ],
  },
}
