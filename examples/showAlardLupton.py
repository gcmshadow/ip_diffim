# This file is part of ip_diffim.
#
# Developed for the LSST Data Management System.
# This product includes software developed by the LSST Project
# (https://www.lsst.org).
# See the COPYRIGHT file at the top-level directory of this distribution
# for details of code ownership.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

import lsst.ip.diffim as ipDiffim
import lsst.afw.display as afwDisplay
import lsst.afw.image as afwImage
import numpy as num

klist = ipDiffim.makeAlardLuptonBasisList(15, 3, [2, 4, 8], [4, 3, 2])
frame = 1
for kernel in klist:
    kImageOut = afwImage.ImageD(kernel.getDimensions())
    kSum = kernel.computeImage(kImageOut, False)
    afwDisplay.Display(frame=frame).mtv(kImageOut, title="Kernal Image")
    frame += 1

kim1 = afwImage.ImageD(klist[0].getDimensions())
kim2 = afwImage.ImageD(klist[0].getDimensions())

for k1 in range(0, len(klist)):
    klist[k1].computeImage(kim1, False)
    # Only first term should have sum != 0.0
    print(k1, num.sum(num.ravel(kim1.getArray())))

print()

for k1 in range(0, len(klist)):
    klist[k1].computeImage(kim1, False)
    arr1 = kim1.getArray().ravel()

    for k2 in range(k1, len(klist)):
        klist[k2].computeImage(kim2, False)
        arr2 = kim2.getArray().ravel()
        # Not orthonormal tho
        print(k1, k2, num.inner(arr1, arr2))
