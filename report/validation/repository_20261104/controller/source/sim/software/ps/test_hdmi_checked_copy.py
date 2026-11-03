import unittest
import numpy as np
from hdmi_display.pixels import copy_rgbx_checked


class CheckedCopyTests(unittest.TestCase):
    def test_exact_pixels_strides_guards_and_all_discarded_positions(self):
        rng=np.random.default_rng(1811)
        for half in (False,True):
            for width in (2,6,16,18,32,38,1920):
                height=8 if width<1920 else 1080
                backing=rng.integers(0,256,(height,width+8,4),np.uint8)
                source=backing[:,:width]
                source[...,3]=0
                original=backing.copy()
                h,w=(height//2,width//2) if half else (height,width)
                destination=np.full((h+2,w+8,4),91,np.uint8)
                view=destination[1:-1,4:w+4]
                self.assertTrue(copy_rgbx_checked(source,view))
                np.testing.assert_array_equal(view,source[::2,::2] if half else source)
                np.testing.assert_array_equal(backing,original)
                np.testing.assert_array_equal(destination[[0,-1]],91)
                np.testing.assert_array_equal(destination[:,:4],91)
                np.testing.assert_array_equal(destination[:,w+4:],91)
                # Odd rows/columns are omitted from half-size RGB, but their X
                # bytes must still be checked. Include vector/scalar tails.
                for y,x in ((0,0),(0,1),(1,0),(1,1),(height-1,width-1),(2,width-2)):
                    source[y,x,3]=128
                    self.assertFalse(copy_rgbx_checked(source,view),(half,width,y,x))
                    source[y,x,3]=0

    def test_invalid_geometry_and_aliasing(self):
        source=np.zeros((8,16,4),np.uint8)
        for destination in (source,source[::2,::2],np.empty((4,7,4),np.uint8)):
            with self.assertRaises(ValueError):copy_rgbx_checked(source,destination)


if __name__=='__main__':unittest.main()
