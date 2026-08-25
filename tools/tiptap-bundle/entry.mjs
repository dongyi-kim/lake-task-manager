// One browser entry for the complete editor runtime. esbuild tree-shakes package internals and
// keeps one @tiptap/core / ProseMirror instance instead of exposing hundreds of CDN mirror files.
import { Editor, Extension, Node, textblockTypeInputRule, InputRule } from "@tiptap/core";
import StarterKit from "@tiptap/starter-kit";
import Mention from "@tiptap/extension-mention";
import { Table, TableRow, TableCell, TableHeader } from "@tiptap/extension-table";
import Image from "@tiptap/extension-image";
import Placeholder from "@tiptap/extension-placeholder";
import CodeBlockLowlight from "@tiptap/extension-code-block-lowlight";
import Suggestion from "@tiptap/suggestion";
import TextAlign from "@tiptap/extension-text-align";
import { TextStyle } from "@tiptap/extension-text-style";
import FontFamily from "@tiptap/extension-font-family";
import TaskList from "@tiptap/extension-task-list";
import TaskItem from "@tiptap/extension-task-item";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { findWrapping } from "@tiptap/pm/transform";
import { common, createLowlight } from "lowlight";
import hljs from "highlight.js/lib/common";

const lowlight = createLowlight(common);
const version = "3.30.3";
const languages = lowlight.listLanguages();

export {
  version, Editor, Extension, Node, textblockTypeInputRule, InputRule, StarterKit, Mention,
  Table, TableRow, TableCell, TableHeader, Image, Placeholder, Plugin, PluginKey,
  CodeBlockLowlight, Suggestion, TextAlign, TextStyle, FontFamily, findWrapping,
  TaskList, TaskItem, lowlight, languages, hljs,
};
