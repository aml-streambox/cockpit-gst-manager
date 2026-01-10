SUMMARY = "GStreamer management Cockpit plugin with AI-assisted pipeline generation"
DESCRIPTION = "A Cockpit plugin for managing multiple GStreamer streaming/encoding pipelines on Amlogic A311D2"
HOMEPAGE = "https://github.com/anshi233/cockpit-gst-manager"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://${COMMON_LICENSE_DIR}/MIT;md5=0835ade698e0bcf8506ecda2f7b4f302"

SRC_URI = " \
    file://gst-manager \
    file://gst-manager.service \
"

S = "${WORKDIR}"

inherit systemd

SYSTEMD_SERVICE:${PN} = "gst-manager.service"
SYSTEMD_AUTO_ENABLE = "enable"

RDEPENDS:${PN} = " \
    python3 \
    python3-dbus \
    python3-json \
    python3-asyncio \
    python3-logging \
    cockpit \
    cockpit-bridge \
    cockpit-ws \
    gstreamer1.0 \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    gstreamer1.0-plugins-bad \
"

do_install() {
    # Backend Python files
    install -d ${D}${libdir}/gst-manager
    cp -r ${WORKDIR}/gst-manager/backend/* ${D}${libdir}/gst-manager/
    
    # Frontend Cockpit plugin
    install -d ${D}${datadir}/cockpit/gst-manager
    cp -r ${WORKDIR}/gst-manager/frontend/* ${D}${datadir}/cockpit/gst-manager/
    
    # systemd service
    install -d ${D}${systemd_system_unitdir}
    install -m 0644 ${WORKDIR}/gst-manager.service ${D}${systemd_system_unitdir}/
    
    # Runtime directories
    install -d ${D}${localstatedir}/lib/gst-manager
    install -d ${D}${localstatedir}/lib/gst-manager/instances
    
    # Default config (empty)
    install -m 0600 -d ${D}${localstatedir}/lib/gst-manager
}

FILES:${PN} = " \
    ${libdir}/gst-manager \
    ${datadir}/cockpit/gst-manager \
    ${localstatedir}/lib/gst-manager \
"

CONFFILES:${PN} = "${localstatedir}/lib/gst-manager/config.json"
