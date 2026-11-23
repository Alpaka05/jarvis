import { mount } from "svelte";
import App from "./App.svelte";
import "./hud.css";
import { connect } from "./store.svelte";

mount(App, { target: document.getElementById("app")! });
connect();
