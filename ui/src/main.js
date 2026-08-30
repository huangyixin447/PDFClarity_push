import { createApp } from "vue";
import { createPinia } from "pinia";
import App from "./App.vue";

// Element Plus 按需引入(unplugin-vue-components),不再全量 use() 与引 index.css。
createApp(App).use(createPinia()).mount("#app");
