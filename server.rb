require 'webrick'
port = (ENV['PORT'] || 8080).to_i
root = File.expand_path(File.dirname(__FILE__))
server = WEBrick::HTTPServer.new(DocumentRoot: root, Port: port, AccessLog: [])
trap('INT') { server.shutdown }
trap('TERM') { server.shutdown }
server.start
