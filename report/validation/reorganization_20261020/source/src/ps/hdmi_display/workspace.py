"""Local mouse workspace. Mode/algorithm scale remain physical controls."""

from dataclasses import replace
from pathlib import Path
import time
import uuid

import cv2
import numpy as np
from PIL import Image

from roi_magnifier.geometry import Rect
from .geometry import Viewport
from .compositor import resize_nearest, Compositor
from .pixels import blit_rgb
from .image_export import export_image
from .media import sha256_file
from .file_picker import VideoPicker


class Workspace:
    def __init__(self, runtime, images, results, *, identity=None, clock=time.monotonic, media_roots=None,
                 session_deadline=None, session_manual=False):
        self.runtime, self.images = runtime, list(map(Path, images))
        self.results = Path(results)
        self.identity = identity or {}
        self.clock = clock
        self.index = 0
        self.selecting = False
        self.drag = None
        self.preview_roi = None
        self.message = "BTN0 Image  BTN1 Video  BTN2 Camera  BTN3 Idle"
        self.notice = None
        self.session_deadline = session_deadline
        self.session_manual = session_manual
        self.width = runtime.engine.display_mode.width
        self.height = runtime.engine.display_mode.height
        self.pointer = (self.width // 2, self.height // 2)
        self.previous = None
        self.last_activity = clock()
        self.last_redraw = 0
        self.visible = True
        self.mode_key = None
        self.exports = []
        self.media_roots = list(media_roots) if media_roots is not None else [
            Path('/home/xilinx/sr_lut_media'), Path('/media/xilinx'), Path('/run/media/xilinx')]
        self.picker = None
        self._camera_ui_key = self._camera_ui_cache = None
        self._camera_static_key = None
        self._selection_image = self._selection_base = None
        self._selection_ui_key = self._selection_ui_cache = None
        self._snapshot_seen = None
        from .metrics import DisplayRate
        from .metric_overlay import CompareHeader
        self.display_rate, self.compare_header = DisplayRate(), CompareHeader()
        self._rate_sample = -1
        self._compare_image_key = self._compare_image_pixels = None
        self.runtime.view_layout = self.compare_layout

    def compare_layout(self, pair=None, config=None):
        rt = self.runtime
        pair = pair if pair is not None else rt.last_pair
        config = config if config is not None else rt.composition
        if (pair is None or config.layout != 'split' or self.replaces_content()
                or rt.engine.mode not in ('image','video','camera')):
            return None
        from .compare_layout import CompareLayout
        return CompareLayout.build(pair,self.width,self.height,rt.engine.mode,config.pixel_view,self.visible)

    def active_button(self, name):
        return name == self.runtime.composition.layout or (name == 'freeze' and self.runtime.camera_frozen)

    def enabled_button(self, name):
        rt = self.runtime
        if name == 'freeze':
            return rt.last_pair is not None and rt.snapshot_status()['state'] != 'running'
        if name == 'snapshot':
            return rt.camera_frozen and rt.snapshot_status()['state'] != 'running'
        return True

    def content_bounds(self):
        rt = self.runtime
        return Compositor.view_bounds(rt.last_pair, self.width, self.height, rt.composition,self.compare_layout())

    def replaces_content(self):
        return self.picker is not None or (self.selecting and self.runtime.image is not None
                                           and self.runtime.engine.mode == 'image')

    def _selection_background(self):
        image = self.runtime.image
        if self._selection_image is not image or self._selection_base is None:
            canvas = np.empty((self.height, self.width, 4), np.uint8)
            canvas[:] = (17, 23, 32, 0)
            box = self.viewport().bounds
            blit_rgb(canvas[box.y:box.bottom, box.x:box.right],
                     resize_nearest(image, (box.width, box.height)))
            self._selection_image, self._selection_base = image, canvas
        return self._selection_base

    def notify(self, message, kind):
        self.message = message
        self.notice = dict(message=message, kind=kind, until=self.clock()+10)
        self.runtime.request_redraw()

    def status(self):
        return dict(message=self.message, last_notice=self.notice, session_manual=self.session_manual,
                    exports=len(self.exports),
                    session_remaining_seconds=(max(0, round(self.session_deadline-self.clock()))
                                               if self.session_deadline is not None else None))

    def load_image(self, index):
        if getattr(self.runtime, 'image_pairs', None) is not None:
            self.runtime.load_paired_image(index)
            self.index = self.runtime.image_pair_index
            self.message = self.runtime.image_record['name']
            return
        path = self.images[index % len(self.images)]
        with Image.open(path) as image:
            if max(image.size) > 4096:
                raise ValueError("Image must be no larger than 4096 x 4096")
            pixels = np.asarray(image.convert("RGB"))
        self.runtime.set_image(pixels)
        self.index = index % len(self.images)
        self.message = path.name

    def point(self, x, y):
        return round(x * self.width / 1280), round(y * self.height / 720)

    def rect(self, x, y, width, height):
        left, top = self.point(x, y)
        right, bottom = self.point(x + width, y + height)
        return Rect(left, top, right - left, bottom - top)

    def viewport(self):
        h, w = self.runtime.image.shape[:2]
        return Viewport.fit(w, h, self.rect(20, 85, 1240, 500))

    def buttons(self):
        if self.picker is not None:
            return [(name, label, self.rect(12+i*170, 635, 162, 42)) for i,(name,label) in enumerate(
                [('files_home','Folders'), ('files_up','Up'), ('files_prev','Previous page'),
                 ('files_next','Next page'), ('files_close','Close')])]
        base = [("sr", "SR full"), ("split", "Compare"), ("baseline", "Nearest"),
                ("wipe", "Wipe"), ("local", "View: 1:1" if self.runtime.composition.pixel_view else "View: Fit"),
                ("view_reset", "Reset view")]
        if self.runtime.engine.mode == "image":
            base += [("roi", "ROI / View"), ("reset", "Reset ROI"),
                     ("previous", "Prev image"), ("next", "Next image"), ("save", "Save PNG")]
        elif self.runtime.engine.mode == "video":
            if self.runtime.import_active:
                base += [('cancel_import', 'Cancel import')]
            elif self.runtime.export_active:
                base += [("cancel_export", "Cancel export")]
            else:
                base += [("play", "Play/Pause"), ("back", "Frame -"), ("forward", "Frame +"),
                         ("restart", "Restart"), ("speed", "Speed"), ("loop", "Loop"), ("export", "Export MP4"),
                         ('open_video', 'Open video')]
        elif self.runtime.engine.mode == 'camera':
            base += [('freeze', 'Resume' if self.runtime.camera_frozen else 'Freeze'),
                     ('snapshot', 'Saving...' if self.runtime.snapshot_status()['state'] == 'running' else 'Save PNG')]
        pitch = min(112, 1256 // len(base))
        return [(action, label, self.rect(12 + i * pitch, 635, pitch-6, 42))
                for i, (action, label) in enumerate(base)]

    def action(self, name):
        rt = self.runtime
        if not self.enabled_button(name):
            return
        config = rt.composition
        if name == 'cancel_import':
            rt.cancel_video_import()
        elif name == 'open_video':
            if rt.export_active or rt.import_active: raise RuntimeError('Cancel the current operation first')
            if rt.playback is not None: rt.playback.pause()
            self.picker = VideoPicker(self.media_roots)
        elif name.startswith('files_') and self.picker is not None:
            {'files_home': self.picker.home, 'files_up': self.picker.up,
             'files_prev': lambda:self.picker.turn(-1), 'files_next': lambda:self.picker.turn(1),
             'files_close': lambda:setattr(self, 'picker', None)}[name]()
        elif name.startswith('pick_') and self.picker is not None:
            source = self.picker.choose(int(name[5:]))
            if source is not None:
                rt.start_video_import(source, self.results/('clip_'+uuid.uuid4().hex[:12]))
                self.picker = None
        elif name == 'cancel_export':
            rt.cancel_video_export()
        elif name == 'export':
            scale = rt.engine.scale
            directory = Path(rt.engine.overlays[scale])
            identity = dict(backend='fpga', package_sha256=self.identity.get('manifest_sha256', ''),
                bit_sha256=sha256_file(directory / f'sr_lut_x{scale}.bit'),
                hwh_sha256=sha256_file(directory / f'sr_lut_x{scale}.hwh'))
            rt.start_video_export(self.results / ('video_'+uuid.uuid4().hex[:12]), processing_identity=identity,
                                  retain_raw_output=rt.profile.dp is not None)
        elif (rt.export_active or rt.import_active) and name not in ('sr', 'split', 'baseline', 'wipe', 'local', 'view_reset'):
            raise RuntimeError('Cancel the current operation before changing playback')
        elif name in ("sr", "split", "baseline", "wipe"):
            self.selecting = False
            self.drag, self.preview_roi = None, None
            rt.set_composition(replace(config, layout=name))
        elif name == "local":
            rt.set_composition(replace(config, pixel_view=not config.pixel_view))
        elif name == 'view_reset':
            self.drag, self.preview_roi = None, None
            rt.set_composition(replace(config, pixel_view=False, pan_x=0, pan_y=0, wipe_fraction=.5))
        elif name == 'freeze' and rt.engine.mode == 'camera':
            rt.resume_camera() if rt.camera_frozen else rt.freeze_camera()
        elif name == 'snapshot' and rt.engine.mode == 'camera':
            rt.save_snapshot(self.results / ('camera_'+uuid.uuid4().hex[:12]), self.identity)
        elif rt.engine.mode == "image":
            if name == "roi":
                self.selecting = not self.selecting
            elif name == "reset":
                rt.reset_roi()
            elif name in ("previous", "next"):
                self.load_image(self.index + (1 if name == "next" else -1))
            elif name == "save":
                if rt.last_pair is None or rt.image_dirty:
                    raise RuntimeError("Wait for the current ROI result before saving")
                record = export_image(rt.last_pair, self.results / ("image_" + uuid.uuid4().hex[:12]),
                    reference_rgb=rt.quality_reference() if getattr(rt,'image_reference',None) is not None else None,
                    source_identity={**self.identity, "source_name": self.images[self.index].name,
                                     "source_sha256": sha256_file(getattr(rt,'image_source_path',None) or self.images[self.index]),
                                     **({'reference_origin':rt.image_record['hr']} if getattr(rt,'image_reference',None) is not None else {}),
                                     "roi": vars(rt.roi)})
                self.exports.append(str(record))
                self.notify("Saved PNG #{} | x{} | {}".format(
                    len(self.exports), rt.last_pair.scale, record.parent.name), 'saved')
        elif rt.engine.mode == "video" and rt.playback is not None:
            player = rt.playback
            if name == "play":
                player.pause() if player.playing else player.play()
            elif name in ("back", "forward"):
                rt.step_video(1 if name == "forward" else -1)
            elif name == "restart":
                rt.seek(0)
            elif name == "speed":
                player.set_speed({0.5: 1.0, 1.0: 2.0, 2.0: 0.5}[player.speed])
            elif name == "loop":
                player.set_loop(not player.loop)
        rt.request_redraw()

    def _press(self, point):
        rt = self.runtime
        if self.visible:
            for name, _, bounds in self.buttons():
                if bounds.x <= point[0] < bounds.right and bounds.y <= point[1] < bounds.bottom:
                    self.action(name)
                    return
            if point[1] <= self.point(0,72)[1] or point[1] >= self.point(0,625)[1]:
                return
        if self.picker is not None:
            for i, _ in enumerate(self.picker.visible):
                box = self.rect(24, 140+i*58, 1232, 50)
                if box.x <= point[0] < box.right and box.y <= point[1] < box.bottom:
                    self.action('pick_'+str(i))
                    break
            return
        if (rt.engine.mode == "video" and rt.playback and not (rt.export_active or rt.import_active) and
                self.point(0, 600)[1] <= point[1] <= self.point(0, 620)[1]):
            left, right = self.point(24, 0)[0], self.point(1256, 0)[0]
            ratio = max(0, min(1, (point[0] - left) / (right - left)))
            rt.seek(round(ratio * (rt.playback.clip.frame_count - 1)))
        elif self.selecting and rt.engine.mode == "image" and self.viewport().contains(*point):
            view = self.viewport()
            x, y = view.source_point(*point)
            roi = rt.roi
            shown = view.display_rect(roi)
            kind = ("new" if roi == Rect(0, 0, view.source_width, view.source_height)
                    else "resize" if abs(point[0] - shown.right) < 14 and abs(point[1] - shown.bottom) < 14
                    else "move" if roi.x <= x < roi.right and roi.y <= y < roi.bottom else "new")
            self.drag = (kind, point, (x, y), roi)
            self.preview_roi = roi
        elif rt.composition.layout == "wipe" and rt.last_pair is not None:
            box = self.content_bounds()
            if box.x <= point[0] < box.right and box.y <= point[1] < box.bottom:
                self.drag = ('wipe', None, None, None)
                self._motion(point)

    def _motion(self, point):
        if self.drag is None:
            return
        kind, initial, source, roi = self.drag
        if kind == 'wipe':
            box = self.content_bounds()
            self.runtime.set_composition(replace(self.runtime.composition,
                wipe_fraction=max(0, min(1, (point[0]-box.x)/box.width))))
            return
        view = self.viewport()
        x, y = view.source_point(*point)
        if kind == "new":
            self.preview_roi = view.selection(initial, point, self.runtime.engine.scale)
        elif kind == "move":
            self.preview_roi = Rect(max(0, min(view.source_width - roi.width, roi.x + x - source[0])),
                                    max(0, min(view.source_height - roi.height, roi.y + y - source[1])),
                                    roi.width, roi.height)
        else:
            self.preview_roi = Rect(roi.x, roi.y, max(1, x - roi.x + 1), max(1, y - roi.y + 1))

    def poll(self, mouse):
        rt = self.runtime
        saved = rt.snapshot_status()
        saved_key = (saved.get('destination'), saved['state'])
        if saved_key != self._snapshot_seen:
            self._snapshot_seen = saved_key
            if saved['state'] == 'complete':
                self.exports.append(saved['report'])
                self.notify('Saved camera PNG | '+Path(saved['report']).parent.name, 'saved')
            elif saved['state'] == 'failed':
                self.notify('Camera save failed: '+saved['error'], 'error')
        key = (rt.engine.mode, rt.engine.scale, rt.engine.generation)
        if self.mode_key is not None and key[:2] != self.mode_key[:2]:
            self.selecting, self.drag, self.preview_roi = False, None, None
            self.picker = None
        self.mode_key = key
        point = (mouse["x"], mouse["y"])
        active = point != self.pointer or mouse.get("transitions") or mouse.get("wheel") != (self.previous or {}).get("wheel")
        if active:
            self.last_activity = self.clock()
        self.pointer = point
        was_visible = self.visible
        self.visible = self.picker is not None or rt.import_active or rt.export_active or self.selecting or self.clock() - self.last_activity < 5 or point[1] >= self.point(0, 590)[1]
        if was_visible != self.visible and rt.composition.pixel_view:
            rt.set_composition(rt.composition)
        try:
            lost = not mouse.get('connected') or mouse.get('dropped', 0) != (self.previous or {}).get('dropped', 0)
            if lost:
                self.drag, self.preview_roi = None, None
            # Ordered transitions preserve short press/release clicks in a single poll.
            for edge in (() if lost else mouse.get("transitions", [])):
                if edge["button"] != 272:
                    continue
                position = (edge["x"], edge["y"])
                if edge["pressed"]:
                    self._press(position)
                elif self.drag is not None:
                    self._motion(position)
                    if self.drag[0] != 'wipe':
                        rt.set_roi(self.preview_roi)
                    self.drag, self.preview_roi = None, None
            if self.drag is not None:
                if not mouse.get("connected") or mouse.get("dropped", 0) != (self.previous or {}).get("dropped", 0):
                    self.drag, self.preview_roi = None, None
                else:
                    self._motion(point)
            wheel = mouse.get("wheel", 0) - (self.previous or mouse).get("wheel", 0)
            if wheel and rt.composition.pixel_view:
                rt.set_composition(replace(rt.composition, pan_x=max(0, rt.composition.pan_x + wheel * 32)))
            if not lost and self.drag is None and 273 in mouse.get("buttons", []) and self.previous and rt.composition.pixel_view:
                rt.set_composition(replace(rt.composition,
                    pan_x=max(0, rt.composition.pan_x + self.previous["x"] - point[0]),
                    pan_y=max(0, rt.composition.pan_y + self.previous["y"] - point[1])))
        except (ValueError, RuntimeError, OSError) as exc:
            self.notify(str(exc), 'error')
            if self.picker is not None: self.picker.message = str(exc)
            self.drag, self.preview_roi = None, None
            rt.request_redraw()
        if active or was_visible != self.visible or self.clock() - self.last_redraw >= 1:
            rt.request_redraw()
            self.last_redraw = self.clock()
        self.previous = dict(mouse)

    def text(self, pixels, text, point, size=0.55, color=(230, 235, 240, 0), *, physical=False):
        cv2.putText(pixels, str(text), point if physical else self.point(*point),
                    cv2.FONT_HERSHEY_SIMPLEX, size * self.height / 720, color, 1, cv2.LINE_AA)

    def camera_cache_key(self):
        rt = self.runtime
        if (rt.engine.mode != 'camera' or rt.last_pair is None or self.picker is not None
                or self.selecting):
            return None
        now = self.clock()
        notice = (self.notice['message'], self.notice['kind']) if self.notice and now < self.notice['until'] else None
        dynamic = (self.pointer, rt.composition.pan_x, rt.composition.pan_y, rt.composition.wipe_fraction,
                   repr(self.display_rate.value) if rt.composition.layout == 'split' else None)
        return (self.visible, dynamic, self.message, self.session_manual,
               max(0, round(self.session_deadline-now)) if self.session_deadline is not None else None,
               notice, rt.engine.scale, rt.engine.display_mode.name, rt.composition.layout,
               rt.composition.pixel_view, rt.camera_frozen, rt.snapshot_status()['state'])

    def camera_content_band(self):
        """Rows not wholly covered by the cached opaque toolbar pixels."""
        if self.camera_cache_key() != self._camera_ui_key or self._camera_ui_cache is None:
            raise RuntimeError('Camera overlay changed before partial repaint')
        _, spans = self._camera_ui_cache
        stride = self.width*4
        first, last = 0, self.height
        if len(spans):
            start, length = map(int, spans[0])
            if start == 0:
                first = length//stride
            start, length = map(int, spans[-1])
            if start+length == self.height*stride:
                last = (start+stride-1)//stride
        return min(first,last), last

    def draw_camera_band(self, pixels, first, last):
        if self.camera_cache_key() != self._camera_ui_key or self._camera_ui_cache is None:
            raise RuntimeError('Camera overlay changed before partial repaint')
        from .pixels import overlay_spans
        canvas, spans = self._camera_ui_cache
        low, high = first*self.width*4, last*self.width*4
        start = np.maximum(spans[:, 0], low)
        stop = np.minimum(spans[:, 0]+spans[:, 1], high)
        keep = stop > start
        clipped = np.column_stack((start[keep], stop[keep]-start[keep])).astype(np.uint32)
        overlay_spans(canvas, pixels, clipped)
        self._partial_camera = True
        try:
            self._draw_view_details(pixels)
        finally:
            self._partial_camera = False
        self._draw_pointer(pixels)

    def draw(self, pixels):
        if self.selecting and self.runtime.image is not None and self.runtime.engine.mode == 'image' and self.picker is None:
            rt, now = self.runtime, self.clock()
            notice = (self.notice['message'], self.notice['kind']) if self.notice and now < self.notice['until'] else None
            key = (id(rt.image), rt.roi, rt.engine.scale, self.visible, self.message,
                   self.session_manual, notice, rt.composition.layout, rt.composition.pixel_view,
                   max(0, round(self.session_deadline-now)) if self.session_deadline is not None else None)
            if key != self._selection_ui_key:
                canvas = np.empty((self.height,self.width,4),np.uint8)
                self._draw_uncached(canvas,pointer=False,selection_box=False)
                self._selection_ui_key, self._selection_ui_cache = key, canvas
            np.copyto(pixels,self._selection_ui_cache)
            self._draw_selection_box(pixels)
            self._draw_view_details(pixels)
            if notice is not None:
                left,top=self.point(120,80)
                right,bottom=self.point(1160,140)
                pixels[top:bottom+1,left:right+1]=self._selection_ui_cache[top:bottom+1,left:right+1]
            self._draw_pointer(pixels)
            return
        key = self.camera_cache_key()
        if key is None:
            return self._draw_uncached(pixels)
        # Pointer motion must not allocate/scan a whole 1080p overlay mask.
        # The engine still receives the complete key, including the pointer,
        # so changing it invalidates partial repaint for every scanout slot.
        static_key = key[:1] + key[2:]
        if static_key != self._camera_static_key:
            canvas = np.zeros((self.height, self.width, 4), np.uint8)
            canvas[..., 3] = 255  # All real drawing explicitly writes X=0.
            self._draw_uncached(canvas, pointer=False, dynamic=False)
            if self.runtime.composition.layout == 'split':
                self.compare_header.draw(canvas,'camera',self.runtime.last_pair,{},self.display_rate.value,self.compare_layout())
            mask = (canvas[..., 3] == 0).reshape(-1)
            edges = np.flatnonzero(np.diff(np.r_[False, mask, False]))
            spans = np.column_stack((edges[::2]*4, (edges[1::2]-edges[::2])*4)).astype(np.uint32)
            self._camera_ui_cache = (canvas, spans)
            self._camera_static_key = static_key
        self._camera_ui_key = key
        from .pixels import overlay_spans
        overlay_spans(self._camera_ui_cache[0], pixels, self._camera_ui_cache[1])
        self._draw_view_details(pixels)
        self._draw_pointer(pixels)

    def _draw_uncached(self, pixels, *, pointer=True, selection_box=True, dynamic=True):
        if pixels.shape != (self.height, self.width, 4):
            raise ValueError('Workspace canvas and display mode disagree')
        rt = self.runtime
        if self.picker is not None:
            pixels[:] = (17,23,32,0)
            title = str(self.picker.directory) if self.picker.directory else 'Local video folders'
            self.text(pixels, title[:100], (24,115), .6)
            for i,path in enumerate(self.picker.visible):
                box = self.rect(24,140+i*58,1232,50)
                cv2.rectangle(pixels,(box.x,box.y),(box.right,box.bottom),(45,58,74,0),-1)
                label = str(path) if self.picker.directory is None else path.name
                self.text(pixels, label[:110], (36,172+i*58), .6)
            self.text(pixels, self.picker.message, (24,585), .5)
        elif self.selecting and rt.image is not None and rt.engine.mode == "image":
            np.copyto(pixels, self._selection_background())
            if selection_box:
                self._draw_selection_box(pixels)
            self.text(pixels, "Drag outside ROI to select; inside to move; bottom-right handle to resize", (24, 610))
        elif rt.last_pair is None:
            message = {"idle": "Ready - press BTN0, BTN1 or BTN2", "camera": "Waiting for USB camera - retrying automatically",
                       "video": "Select video with BTN1", "image": "Processing image"}[rt.engine.mode]
            self.text(pixels, message, (190, 360), 0.8)
        compare = self.compare_layout() is not None
        if self.visible:
            if not compare:
                cv2.rectangle(pixels, (0, 0), (self.width - 1, self.point(0, 72)[1]), (17, 23, 32, 0), -1)
            cv2.rectangle(pixels, self.point(0, 625), (self.width - 1, self.height - 1), (17, 23, 32, 0), -1)
            if not compare:
                self.text(pixels, "SR-LUT | {} | algorithm x{} | HDMI {}".format(
                    rt.engine.mode.upper(), rt.engine.scale, rt.engine.display_mode.name), (22, 27), 0.7)
            if not compare and rt.roi is not None and rt.engine.mode == "image":
                self.text(pixels, "Source {}x{}  ROI ({},{}) {}x{}".format(rt.image.shape[1], rt.image.shape[0],
                          rt.roi.x, rt.roi.y, rt.roi.width, rt.roi.height), (22, 55))
            elif rt.playback is not None and rt.engine.mode == "video":
                p = rt.playback
                if not compare:
                    self.text(pixels, "Frame {} / {}   {}x speed   loop {}".format(p.index, p.clip.frame_count, p.speed, p.loop), (22, 55))
                if self.picker is None:
                    cv2.line(pixels, self.point(24, 610), self.point(1256, 610), (100, 110, 120, 0), 4)
                    x = 24 + round(1232 * p.index / max(1, p.clip.frame_count - 1))
                    cv2.circle(pixels, self.point(x, 610), 7, (30, 230, 190, 0), -1)
            if not compare and rt.engine.mode == 'camera':
                self.text(pixels, 'FROZEN - same frame' if rt.camera_frozen else 'LIVE', (22,55), .6)
            for action, label, bounds in self.buttons():
                color = (35,115,85,0) if self.active_button(action) else (45,58,74,0)
                enabled = self.enabled_button(action)
                cv2.rectangle(pixels, (bounds.x, bounds.y), (bounds.right, bounds.bottom), color if enabled else (30,35,44,0), -1)
                dx, dy = self.point(7, 26)
                self.text(pixels, label, (bounds.x + dx, bounds.y + dy), 0.45,
                          color=(230,235,240,0) if enabled else (105,110,115,0), physical=True)
            export = rt.export_status
            message = self.message
            importing = rt.import_status
            if rt.engine.mode == 'video' and importing['state'] != 'idle' and export['state'] != 'running':
                if importing['state'] == 'running':
                    message = 'Import: '+importing['phase']
                    if importing['phase'] == 'decoding' and importing['total']:
                        message += ' ~{}%'.format(min(99, round(importing['done']*100/importing['total'])))
                    message += ' | Cancel import or press a board button to stop'
                elif importing['state'] == 'complete': message = 'Video imported | Playback paused'
                elif importing['state'] == 'failed': message = 'Import failed: '+importing['error']
                else: message = 'Import cancelled | Previous video kept'
            elif rt.engine.mode == 'video' and export['state'] != 'idle':
                if export['state'] == 'running':
                    phase = export['phase']
                    message = ('Export: {} / {} frames'.format(export['done'], export['total'])
                               if phase == 'processing' else 'Export: '+phase)
                    message += ' | Cancel export or press a board button to stop'
                elif export['state'] == 'complete':
                    message = 'Saved MP4: '+Path(export['report']).parent.name+' | Playback paused'
                elif export['state'] == 'failed':
                    message = 'Export failed: '+export['error']
                else:
                    message = 'Export cancelled | Playback paused'
            self.text(pixels, message[:140], (18, 704), 0.45)
        if compare and (self.session_manual or self.session_deadline is not None):
            cv2.rectangle(pixels,self.point(650,684),self.point(820,719),(17,23,32,0),-1)
        if self.session_manual:
            self.text(pixels, 'Manual session', (660,704) if compare else (990,28), .45 if compare else .6)
        elif self.session_deadline is not None:
            remaining = max(0, round(self.session_deadline-self.clock()))
            if not compare:
                cv2.rectangle(pixels, self.point(980, 0), self.point(1279, 42), (17, 23, 32, 0), -1)
            self.text(pixels, 'Session ends in {}s'.format(remaining), (660,704) if compare else (990,28), .45 if compare else .6)
        if self.notice is not None and self.clock() < self.notice['until']:
            color = (45, 110, 38, 0) if self.notice['kind'] == 'saved' else (35, 40, 155, 0)
            cv2.rectangle(pixels, self.point(120, 80), self.point(1160, 140), color, -1)
            self.text(pixels, self.notice['message'][:95], (140, 119), .7)
        if dynamic:
            self._draw_view_details(pixels)
        if pointer:
            self._draw_pointer(pixels)

    def _draw_view_details(self, pixels):
        rt, config = self.runtime, self.runtime.composition
        if self.visible and config.layout != 'split':
            # This small opaque field is dynamic; moving it never rebuilds the overlay mask.
            cv2.rectangle(pixels, self.point(820,36), self.point(1279,71), (17,23,32,0), -1)
            label = 'View: 1:1 | Pan: {},{}'.format(config.pan_x, config.pan_y) if config.pixel_view else 'View: Fit'
            self.text(pixels, label, (830,57), .5)
        if config.layout == 'wipe' and rt.last_pair is not None and not self.selecting and self.picker is None:
            box = self.content_bounds()
            x = min(box.right-1, box.x+round(box.width*config.wipe_fraction))
            top = max(box.y, self.point(0,73)[1]) if self.visible else box.y
            bottom = min(box.bottom, self.point(0,625)[1]) if self.visible else box.bottom
            if bottom > top:
                # Clip caps, handle and labels to the visible picture. The
                # toolbar starts at the exclusive bottom of this view.
                view = pixels[top:bottom,box.x:box.right]
                local_x, last = x-box.x, bottom-top-1
                cv2.line(view, (local_x,0), (local_x,last), (30,230,190,0), 2)
                cy = last//2
                cv2.rectangle(view, (max(0,local_x-8),cy-18),
                              (min(box.width-1,local_x+8),cy+18), (30,230,190,0), -1)
                if self.visible:
                    self.text(view, 'Nearest', (12,24), .5, physical=True)
                    self.text(view, 'SR', (box.width-40,24), .5, physical=True)

        self._draw_compare_metrics(pixels)

    def update_metrics(self):
        rt,now=self.runtime,self.clock()
        if rt.engine.mode != 'camera' or rt.composition.layout != 'split':
            self.display_rate.update(now,None,None)
            return
        if now-self._rate_sample < .05:
            return
        self._rate_sample=now
        frozen=getattr(rt,'camera_frozen',False)
        state='frozen' if frozen else 'live' if rt.camera.state=='streaming' else 'disconnected'
        count=getattr(rt.engine.display,'acknowledged_source_pairs',None)
        key=(rt.engine.generation,rt.composition.layout,rt.composition.pixel_view,frozen)
        before=self.display_rate.value
        self.display_rate.update(now,key,count,state)
        if before != self.display_rate.value:
            rt.request_redraw()

    def _draw_compare_metrics(self,pixels):
        rt=self.runtime
        if getattr(self,'_partial_camera',False):
            return
        if rt.composition.layout != 'split' or rt.last_pair is None or self.replaces_content():
            return
        if rt.engine.mode not in ('image','video','camera'):
            return
        quality=rt.quality_status() if hasattr(rt,'quality_status') else {'state':'unavailable'}
        self.compare_header.draw(pixels,rt.engine.mode,rt.last_pair,quality,self.display_rate.value,self.compare_layout())
        if self.visible:
            config=rt.composition
            label='View: 1:1 | Pan: {},{}'.format(config.pan_x,config.pan_y) if config.pixel_view else 'View: Fit'
            cv2.rectangle(pixels,self.point(820,684),(self.width-1,self.height-1),(17,23,32,0),-1)
            self.text(pixels,label,(830,704),.45)

    def _draw_pointer(self, pixels):
        x, y = self.pointer
        cv2.line(pixels, (x, y), (x, y + 18), (0, 0, 0, 0), 5)
        cv2.line(pixels, (x, y), (x + 13, y + 13), (255, 240, 150, 0), 3)

    def _draw_selection_box(self, pixels):
        box = self.viewport().display_rect(self.preview_roi or self.runtime.roi)
        cv2.rectangle(pixels,(box.x,box.y),(box.right-1,box.bottom-1),(30,230,190,0),2)
        cv2.rectangle(pixels,(box.right-7,box.bottom-7),(box.right+4,box.bottom+4),(255,220,80,0),-1)
