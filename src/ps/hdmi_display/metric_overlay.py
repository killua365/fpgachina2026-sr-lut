"""Cached, picture-aligned Compare labels; no full-frame text rendering."""
from pathlib import Path
from functools import lru_cache

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .compare_layout import CompareLayout

BG, FG = (17, 23, 32, 0), (240, 244, 250, 0)
GREEN, AMBER, GRAY = (75, 220, 150, 0), (255, 185, 75, 0), (165, 175, 190, 0)
ACCENT = (81, 200, 217, 0)
FONT_ROOT = Path(__file__).with_name('fonts')


@lru_cache(maxsize=32)
def font(size, bold=False):
    return ImageFont.truetype(str(FONT_ROOT / ('dejavusans-bold.ttf' if bold else 'dejavusans.ttf')), size)


def number(value, decimals):
    return '\u221e' if value == 'infinity' else format(value, '.'+str(decimals)+'f')


class CompareHeader:
    def __init__(self):
        self.key = self.canvas = None
        self.patches = ()
        self._rate_key = None
        self._rate_patch = None
        self.labels = ()

    @staticmethod
    def _patch(box, factor):
        image = Image.new('RGB', (box.width, box.height), BG[:3])
        draw = ImageDraw.Draw(image)
        def put(label, x, y, size=22, color=FG, bold=False, right=False):
            face = font(max(8, round(size*factor)), bold)
            if right:
                x -= draw.textbbox((0,0), label, font=face)[2]
            draw.text((round(x), round(y)), label, font=face, fill=color[:3], anchor='lt')
        return image, draw, put

    @staticmethod
    def _pixels(image):
        result = np.empty((image.height,image.width,4), np.uint8)
        result[...,:3] = np.asarray(image)
        result[...,3] = 0
        return result

    def draw(self, pixels, mode, pair, quality, rate, layout=None):
        width, height = pixels.shape[1], pixels.shape[0]
        factor = width/1920
        layout = layout or CompareLayout.build(pair,width,height,mode,toolbar=False)
        dims = (tuple(pair.source_rgb.shape[1::-1]), tuple(pair.output_rgb.shape[1::-1]))
        key = (width,mode,dims,pair.scale,layout,repr(quality) if mode=='image' else '')
        if key != self.key:
            patches, labels = [], []
            for side, (box, dimension) in enumerate(zip(layout.captions,dims)):
                image, draw, put = self._patch(box,factor)
                title = 'Input / Nearest' if side==0 else 'SR-LUT'
                put(title,0,4*factor)
                labels.append(title)
                if mode != 'image':
                    dimension_text = '{} \u00d7 {}'.format(*dimension)
                    put(dimension_text,200*factor,0,30,bold=True)
                    labels.append(dimension_text)
                if mode == 'image':
                    if quality.get('state') == 'complete':
                        score = quality['nearest' if side==0 else 'sr']
                        for y, label, value in ((42,'PSNR-Y',number(score['psnr_y_db'],2)+' dB'),
                                                (78,'SSIM-Y',number(score['ssim_y'],4))):
                            put(label,0,y*factor,color=GRAY)
                            put(value,200*factor,(y-3)*factor,26)
                            labels += [label,value]
                    else:
                        label = 'Calculating\u2026' if quality.get('state') in ('idle','running') else 'Quality unavailable'
                        put(label,0,48*factor,24,GRAY); labels.append(label)
                patches.append((box,self._pixels(image)))
            if mode == 'image' and quality.get('state') == 'complete':
                box = layout.captions[1]
                image = Image.fromarray(patches[1][1][...,:3]); draw=ImageDraw.Draw(image)
                x = 0
                for name, field, precision, suffix in (('\u0394PSNR','psnr_y_db',2,' dB'),('\u0394SSIM','ssim_y',4,'')):
                    a,b = quality['sr'][field],quality['nearest'][field]
                    delta = a-b if isinstance(a,(int,float)) and isinstance(b,(int,float)) else None
                    label = name+' '+(format(delta,'+.'+str(precision)+'f')+suffix if delta is not None else '--')
                    color = GRAY if delta is None or delta==0 else GREEN if delta>0 else AMBER
                    face=font(max(8,round(20*factor)))
                    draw.text((x,104*factor),label,font=face,fill=color[:3],anchor='lt')
                    x += draw.textbbox((0,0),label,font=face)[2]+round(24*factor)
                    labels.append(label)
                patches[1]=(box,self._pixels(image))
            from roi_magnifier.geometry import Rect
            y=min(b.y for b in layout.captions)
            image_mode = mode == 'image'
            badge_width = round((80 if image_mode else 48)*factor)
            gutter_left, gutter_right = layout.captions[0].right, layout.captions[1].x
            badge_width = min(badge_width, gutter_right-gutter_left)
            badge_x = max(gutter_left,min(width//2-badge_width//2,gutter_right-badge_width))
            box=Rect(badge_x,y,badge_width,round(42*factor))
            image,draw,put=self._patch(box,factor)
            if image_mode:
                label = '\u00d7'+str(pair.scale)
                text_width = draw.textbbox((0,0),label,font=font(max(8,round(30*factor)),True))[2]
                put(label,(box.width-text_width)/2,4*factor,30,bold=True)
            else:
                put('\u00d7'+str(pair.scale),6*factor,0,18,GRAY)
                put('\u2192',8*factor,20*factor,20,GRAY)
            patches.append((box,self._pixels(image)))
            self.key,self.patches,self.labels=key,tuple(patches),tuple(labels)
            self.canvas=patches[0][1]
        for box, canvas in self.patches:
            pixels[box.y:box.bottom,box.x:box.right]=canvas
        if mode == 'camera' and layout.fps is not None:
            rate_key=(layout.fps,repr(rate),factor)
            if rate_key != self._rate_key:
                box=layout.fps; image,draw,put=self._patch(box,factor)
                state,fps=rate.get('state','waiting'),rate.get('fps')
                label=({'frozen':'FROZEN','disconnected':'Disconnected','waiting':'FPS --'}.get(state)
                       or 'FPS {:.1f}'.format(fps))
                put(label,0,2*factor,36,ACCENT)
                left,right,y=round(340*factor),round(680*factor)-1,round(19*factor)
                draw.rectangle((left,y,right,y+round(8*factor)),fill=(55,68,82))
                if fps is not None and fps>0:
                    stop=left+round((right-left)*min(fps,30)/30)
                    draw.rectangle((left,y,stop,y+round(8*factor)),fill=ACCENT[:3])
                put('0',left,37*factor,18,GRAY)
                put('30',right,37*factor,18,GRAY,right=True)
                self._rate_key,self._rate_patch=rate_key,self._pixels(image)
            box=layout.fps
            pixels[box.y:box.bottom,box.x:box.right]=self._rate_patch
